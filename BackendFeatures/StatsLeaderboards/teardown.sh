#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# teardown.sh — Safe, controlled teardown of the Game Stats & Leaderboards stacks.
#
# WHY THIS EXISTS
#   `cdk destroy` alone cannot fully tear this system down:
#     1. Some resources are RETAIN by design (DynamoDB tables, KMS key, layer) — CFN
#        leaves them behind on purpose.
#     2. The MemoryDB cluster deletes ASYNCHRONOUSLY (~15-20 min). CloudFormation can
#        time out waiting, leaving its subnet group / parameter group / ACL / user
#        orphaned in a DELETE_FAILED stack.
#     3. The post-deploy step (app_post_deploy.py) creates provisioned concurrency and
#        application-autoscaling targets imperatively (boto3) — invisible to CFN, so
#        `cdk destroy` never removes them (provisioned concurrency keeps billing).
#
#   This script orchestrates around all three, gives you per-resource control over
#   your DATA (retain / back-up-then-delete / delete), and starts the slow MemoryDB
#   delete FIRST so it drains while the rest of the teardown proceeds.
#
# SAFETY
#   * Nothing is deleted without an explicit typed confirmation up front, plus a
#     per-round choice for each data resource.
#   * --dry-run (DEFAULT) shows exactly what WOULD happen and changes nothing.
#     You must pass --execute to perform real deletions.
#   * The bastion EC2 instance and its VPC are NEVER touched.
#   * Consistency is enforced: you cannot delete a KMS key while retaining the tables
#     it encrypts, nor delete the secret while retaining the MemoryDB cluster.
#
# USAGE
#   ./teardown.sh                      # dry-run (default) — safe preview
#   ./teardown.sh --execute            # perform the teardown (with confirmations)
#   ./teardown.sh --execute --profile myprofile --region us-west-2
#   Options:
#     --execute            Actually perform deletions (omit for dry-run)
#     --profile <name>     AWS CLI profile (default: current credential chain)
#     --region <region>    AWS region (default: $AWS_DEFAULT_REGION or us-west-2)
#     --environment <env>  dev|staging|prod (default: dev)
#     --stack-name <name>  Base stack (default: GameStatsLeaderboardsStack)
#     --yes-i-understand   Pre-answer the top typed gate (for non-interactive runs);
#                          per-resource data choices still default to safe values.

set -euo pipefail

# ----------------------------------------------------------------------------------
# Configuration / arg parsing
# ----------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXECUTE=false
AWS_PROFILE_ARG=""
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
ENVIRONMENT="dev"
BASE_STACK="GameStatsLeaderboardsStack"
PREANSWERED_GATE=false

while [ $# -gt 0 ]; do
    case "$1" in
        --execute) EXECUTE=true; shift ;;
        --dry-run) EXECUTE=false; shift ;;
        --profile) AWS_PROFILE_ARG="--profile $2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --environment) ENVIRONMENT="$2"; shift 2 ;;
        --stack-name) BASE_STACK="$2"; shift 2 ;;
        --yes-i-understand) PREANSWERED_GATE=true; shift ;;
        -h|--help)
            sed -n '2,48p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

SERVICE_NAME="game-statsleaderboards"
PREFIX="${SERVICE_NAME}-${ENVIRONMENT}"
MONITORING_STACK="GameStatsLeaderboardsMonitoringStack"

# Deterministic resource names (must match app.py)
CLUSTER_NAME="${PREFIX}-cluster"
SUBNET_GROUP="${PREFIX}-subnet-group"
PARAM_GROUP="${PREFIX}-glide-params"
ACL_NAME="${PREFIX}-glide-acl"
MDB_USER="glide-user"
SECRET_NAME="${PREFIX}-memorydb-password"
KMS_ALIAS="alias/${PREFIX}-key"
CONFIG_TABLE="${PREFIX}-config"
STATS_TABLE="${PREFIX}-stats"
LAYER_NAME="${PREFIX}-valkey-glide-layer"   # versions are suffixed; we match by prefix
PC_FUNCTIONS=("${PREFIX}-player-store-stats" "${PREFIX}-get-leaderboard-scores" "${PREFIX}-get-player-lb-standing")

# A stamp for backup names. (Bash can't depend on a CDK token; this is a shell-local stamp.)
STAMP="$(date -u +%Y%m%d-%H%M%S 2>/dev/null || echo manual)"

# ----------------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------------
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[1;33m'; BLU=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
info()  { echo "${BLU}[INFO]${NC} $*"; }
ok()    { echo "${GRN}[ OK ]${NC} $*"; }
warn()  { echo "${YEL}[WARN]${NC} $*"; }
err()   { echo "${RED}[FAIL]${NC} $*"; }
hr()    { echo "------------------------------------------------------------------------"; }

# aws wrapper — applies profile + region everywhere, centrally.
aws_() { aws $AWS_PROFILE_ARG --region "$REGION" "$@"; }

# run <description> <cmd...> — echoes in dry-run, executes (with the description) otherwise.
run() {
    local desc="$1"; shift
    if [ "$EXECUTE" = true ]; then
        info "RUN: $desc"
        "$@"
    else
        echo "   ${YEL}[dry-run]${NC} would: $desc"
        return 0
    fi
}

# ----------------------------------------------------------------------------------
# Preconditions
# ----------------------------------------------------------------------------------
# Run from the script's directory so `cdk --app "<python> app.py"` resolves the app.
cd "$SCRIPT_DIR" || { echo "Cannot cd to $SCRIPT_DIR"; exit 1; }

if ! command -v aws >/dev/null 2>&1; then err "AWS CLI not found on PATH."; exit 1; fi
if ! aws_ sts get-caller-identity >/dev/null 2>&1; then
    err "AWS credentials not valid for the requested profile/region. Configure them and retry."
    exit 1
fi
ACCOUNT_ID="$(aws_ sts get-caller-identity --query Account --output text)"
CDK_AVAILABLE=true
command -v cdk >/dev/null 2>&1 || { CDK_AVAILABLE=false; warn "cdk CLI not found — stack deletes will be skipped (orphan sweep still runs). Install/activate cdk for a full teardown."; }

# Resolve a python interpreter for cdk --app (mirrors deploy.sh behaviour).
PYTHON_CMD=""
for c in python3.13 python3 python; do command -v "$c" >/dev/null 2>&1 && { PYTHON_CMD="$c"; break; }; done

# ----------------------------------------------------------------------------------
# Discover the live stack + the bastion-protection guard
# ----------------------------------------------------------------------------------
stack_status() {
    aws_ cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST"
}
MAIN_STATUS="$(stack_status "$BASE_STACK")"
MON_STATUS="$(stack_status "$MONITORING_STACK")"

# Find the VPC this stack owns + whether a bastion instance lives in it (NEVER delete it).
STACK_VPC_ID="$(aws_ ec2 describe-vpcs \
    --filters "Name=tag:aws:cloudformation:stack-name,Values=$BASE_STACK" \
    --query "Vpcs[0].VpcId" --output text 2>/dev/null || echo "None")"
BASTION_PRESENT="no"; BASTION_ID="None"
if [ "$STACK_VPC_ID" != "None" ] && [ -n "$STACK_VPC_ID" ]; then
    BASTION_ID="$(aws_ ec2 describe-instances \
        --filters "Name=vpc-id,Values=$STACK_VPC_ID" "Name=instance-state-name,Values=running,stopped,stopping,pending" \
        --query "Reservations[].Instances[?!contains(to_string(Tags), 'aws:cloudformation:stack-id')].InstanceId | [0]" \
        --output text 2>/dev/null || echo "None")"
    [ -n "$BASTION_ID" ] && [ "$BASTION_ID" != "None" ] && BASTION_PRESENT="yes"
fi

# ----------------------------------------------------------------------------------
# Live-resource existence probes (for the inventory)
# ----------------------------------------------------------------------------------
exists_cluster()  { aws_ memorydb describe-clusters --cluster-name "$CLUSTER_NAME" --query "Clusters[0].Status" --output text 2>/dev/null || echo ""; }
exists_table()    { aws_ dynamodb describe-table --table-name "$1" --query "Table.TableStatus" --output text 2>/dev/null || echo ""; }
table_items()     { aws_ dynamodb describe-table --table-name "$1" --query "Table.ItemCount" --output text 2>/dev/null || echo "?"; }
exists_secret()   { aws_ secretsmanager describe-secret --secret-id "$SECRET_NAME" --query "ARN" --output text 2>/dev/null || echo ""; }
exists_kms()      { aws_ kms describe-key --key-id "$KMS_ALIAS" --query "KeyMetadata.KeyId" --output text 2>/dev/null || echo ""; }
layer_versions()  { aws_ lambda list-layer-versions --layer-name "$LAYER_NAME" --query "LayerVersions[].Version" --output text 2>/dev/null || echo ""; }

# ----------------------------------------------------------------------------------
# Banner + top-level safety gate
# ----------------------------------------------------------------------------------
clear 2>/dev/null || true
echo "${BOLD}================================================================================${NC}"
echo "${BOLD}  GAME STATS & LEADERBOARDS — TEARDOWN${NC}"
echo "${BOLD}================================================================================${NC}"
echo "  Mode:        $( [ "$EXECUTE" = true ] && echo "${RED}EXECUTE (real deletions)${NC}" || echo "${GRN}DRY-RUN (no changes — preview only; pass --execute to perform the teardown)${NC}" )"
echo "  Account:     $ACCOUNT_ID"
echo "  Region:      $REGION"
echo "  Environment: $ENVIRONMENT   Prefix: $PREFIX"
echo "  Main stack:  $BASE_STACK  ($MAIN_STATUS)"
echo "  Monitoring:  $MONITORING_STACK  ($MON_STATUS)"
hr
echo "  ${BOLD}Protected (never touched):${NC}"
if [ "$BASTION_PRESENT" = "yes" ]; then
    echo "    • Bastion EC2 instance $BASTION_ID (in $STACK_VPC_ID) — and its VPC"
else
    echo "    • (no non-stack EC2 instance detected in the stack VPC)"
fi
hr

# Live inventory
CL_STATUS="$(exists_cluster)"
CFG_STATUS="$(exists_table "$CONFIG_TABLE")"
STS_STATUS="$(exists_table "$STATS_TABLE")"
SEC_ARN="$(exists_secret)"
KMS_ID="$(exists_kms)"
LYR_VERS="$(layer_versions)"

echo "  ${BOLD}Discovered resources for this deployment:${NC}"
echo "    MemoryDB cluster   : ${CLUSTER_NAME}  [${CL_STATUS:-absent}]"
echo "    DynamoDB config    : ${CONFIG_TABLE}  [${CFG_STATUS:-absent}, items≈$( [ -n "$CFG_STATUS" ] && table_items "$CONFIG_TABLE" || echo - )]"
echo "    DynamoDB stats     : ${STATS_TABLE}  [${STS_STATUS:-absent}, items≈$( [ -n "$STS_STATUS" ] && table_items "$STATS_TABLE" || echo - )]"
echo "    KMS key (alias)    : ${KMS_ALIAS}  [${KMS_ID:-absent}]"
echo "    Secret             : ${SECRET_NAME}  [$( [ -n "$SEC_ARN" ] && echo present || echo absent )]"
echo "    Lambda layer       : ${LAYER_NAME}  [versions: ${LYR_VERS:-none}]"
echo "    MemoryDB groups    : subnet=${SUBNET_GROUP}, params=${PARAM_GROUP}, acl=${ACL_NAME}, user=${MDB_USER}"
hr

if [ "$EXECUTE" = true ]; then
    echo "${RED}${BOLD}  This will PERMANENTLY delete infrastructure for ${PREFIX}.${NC}"
    echo "  You will choose retain / backup+delete / delete for each DATA resource next."
    if [ "$PREANSWERED_GATE" != true ]; then
        echo ""
        printf "  To proceed, type the stack name exactly (${BOLD}%s${NC}): " "$BASE_STACK"
        read -r CONFIRM
        if [ "$CONFIRM" != "$BASE_STACK" ]; then err "Confirmation did not match. Aborting — nothing was changed."; exit 1; fi
    fi
else
    info "Dry-run: showing what would happen. Re-run with ${BOLD}--execute${NC} to perform the teardown."
fi
echo ""

# ----------------------------------------------------------------------------------
# Per-resource 3-way choice prompt
#   echoes detailed impact, returns one of: retain | backup | delete
#   $1 = choice-variable name, $2 = human label, $3 = impact text, $4 = default (backup|delete)
# ----------------------------------------------------------------------------------
ask_choice() {
    local __var="$1" label="$2" impact="$3" default="${4:-backup}"
    echo "${BOLD}>>> $label${NC}"
    echo "    Impact: $impact"
    if [ "$EXECUTE" != true ]; then
        echo "    ${YEL}[dry-run]${NC} default choice would be: ${BOLD}$default${NC}"
        printf -v "$__var" '%s' "$default"; echo ""; return 0
    fi
    local choice=""
    while true; do
        printf "    Choose [retain | backup | delete] (default: %s): " "$default"
        read -r choice
        choice="${choice:-$default}"
        case "$choice" in
            retain|backup|delete) break ;;
            *) echo "    Please type 'retain', 'backup', or 'delete'." ;;
        esac
    done
    printf -v "$__var" '%s' "$choice"
    echo ""
}

# ----------------------------------------------------------------------------------
# Collect choices up front. IMPORTANT: the KMS key and the Secret are NOT asked as
# free choices — they are DEPENDENT resources whose fate is DERIVED from (and locked
# to) the data resources they protect:
#
#   • Secret  — holds the MemoryDB password. It is ONLY ever deleted if the MemoryDB
#               cluster itself is being deleted. If the cluster is retained, the
#               secret is FORCE-RETAINED (a redeploy needs it; deleting it -> WRONGPASS).
#
#   • KMS key — encrypts the DynamoDB tables AND any on-demand backup of them (a
#               DynamoDB backup inherits the table's customer-managed key, and a
#               RESTORE REQUIRES that key). Therefore the key is ONLY ever deleted if
#               the tables are being deleted WITH NO BACKUP. If the tables are
#               retained OR backed up — or any DynamoDB backup of them exists — the
#               key is FORCE-RETAINED (deleting it would make the data/backups
#               permanently unrecoverable).
#
# This makes the dangerous combinations IMPOSSIBLE to select, rather than merely
# caught afterwards.
# ----------------------------------------------------------------------------------
CHOICE_MEMORYDB="retain"; CHOICE_TABLES="retain"; CHOICE_KMS="retain"
CHOICE_SECRET="retain"; CHOICE_LAYER="delete"; CHOICE_MON="delete"

[ -n "$CL_STATUS" ] && ask_choice CHOICE_MEMORYDB "MemoryDB cluster ($CLUSTER_NAME)" \
    "All cached leaderboard data is lost. 'backup' takes a final manual snapshot (no expiry, billed) before deleting. (Snapshot uses MemoryDB service-managed encryption, not the stack KMS key.)" backup

if [ -n "$CFG_STATUS" ] || [ -n "$STS_STATUS" ]; then
    ask_choice CHOICE_TABLES "DynamoDB tables ($CONFIG_TABLE, $STATS_TABLE)" \
        "Your persisted configs + player stats. 'backup' creates on-demand backups (persist after delete) before deleting." backup
fi

[ -n "$LYR_VERS" ] && ask_choice CHOICE_LAYER "Lambda layer ($LAYER_NAME)" \
    "Rebuildable from layers/build_layer.sh, holds no data. 'delete' removes all versions; 'retain' keeps them." delete

if [ "$MON_STATUS" != "DOES_NOT_EXIST" ]; then
    ask_choice CHOICE_MON "Monitoring stack ($MONITORING_STACK)" \
        "CloudWatch alarms + dashboard. No data. 'delete' removes the stack." delete
fi

# ----------------------------------------------------------------------------------
# PRODUCTION-GRADE DEPENDENCY RESOLVER
#
# The KMS key and the Secret are NEVER deleted if ANYTHING that is being kept (a
# retained resource, a backup we are creating, or a backup that already exists)
# still needs them. We discover the real dependencies from LIVE AWS state (not
# assumptions) — encryption config can differ per deployment — and FORCE-RETAIN
# any dependency, listing every reason. The user is told exactly what was kept and
# why, and can remove those manually later once they no longer need the data.
# ----------------------------------------------------------------------------------
KMS_KEEP_REASONS=()      # why the CMK must be kept
SECRET_KEEP_REASONS=()   # why the secret must be kept

# ----- Discover the stack CMK ARN + every live consumer of it -----------------
KMS_ARN=""
if [ -n "$KMS_ID" ]; then
    KMS_ARN="$(aws_ kms describe-key --key-id "$KMS_ID" --query "KeyMetadata.Arn" --output text 2>/dev/null || echo "")"
fi
kms_used_by() {  # echoes "yes" if $1 (a kms arn/id) equals the stack CMK
    [ -n "$KMS_ARN" ] && { [ "$1" = "$KMS_ARN" ] || [ "$1" = "$KMS_ID" ] || [ "$1" = "arn:aws:kms:${REGION}:${ACCOUNT_ID}:key/${KMS_ID}" ]; } && echo yes || echo no
}

# DynamoDB tables: do they (live) use the stack CMK?
DDB_USES_CMK="no"
for t in "$CONFIG_TABLE" "$STATS_TABLE"; do
    [ -n "$(exists_table "$t")" ] || continue
    karn="$(aws_ dynamodb describe-table --table-name "$t" --query "Table.SSEDescription.KMSMasterKeyArn" --output text 2>/dev/null || echo "")"
    [ "$(kms_used_by "$karn")" = "yes" ] && DDB_USES_CMK="yes"
done

# Existing DynamoDB backups (survive table deletion; restoring them needs the CMK if
# the source table used it).
EXISTING_DDB_BACKUPS=""
for t in "$CONFIG_TABLE" "$STATS_TABLE"; do
    bk="$(aws_ dynamodb list-backups --table-name "$t" --query "BackupSummaries[].BackupArn" --output text 2>/dev/null || echo "")"
    [ -n "$bk" ] && EXISTING_DDB_BACKUPS="$EXISTING_DDB_BACKUPS $bk"
done

# MemoryDB cluster: does it (live) use the stack CMK? (If so, its snapshots need it.)
MDB_USES_CMK="no"
if [ -n "$CL_STATUS" ]; then
    mk="$(aws_ memorydb describe-clusters --cluster-name "$CLUSTER_NAME" --query "Clusters[0].KMSKeyId" --output text 2>/dev/null || echo "")"
    [ "$mk" != "None" ] && [ "$(kms_used_by "$mk")" = "yes" ] && MDB_USES_CMK="yes"
fi

# Existing MemoryDB snapshots for this cluster (survive deletion). Flag any that use
# the stack CMK (restoring those needs it).
EXISTING_MDB_SNAPSHOTS="$(aws_ memorydb describe-snapshots \
    --query "Snapshots[?contains(Name, '${CLUSTER_NAME}')].Name" --output text 2>/dev/null || echo "")"
MDB_SNAP_USES_CMK="no"
if [ -n "$EXISTING_MDB_SNAPSHOTS" ] && [ -n "$KMS_ID" ]; then
    while IFS= read -r snap; do
        [ -z "$snap" ] && continue
        sk="$(aws_ memorydb describe-snapshots --snapshot-name "$snap" --query "Snapshots[0].KmsKeyId" --output text 2>/dev/null || echo "")"
        [ "$sk" != "None" ] && [ "$(kms_used_by "$sk")" = "yes" ] && MDB_SNAP_USES_CMK="yes"
    done <<< "$(printf '%s\n' $EXISTING_MDB_SNAPSHOTS)"
fi

# Secret: does it (live) use the stack CMK? (Then deleting the CMK strands the secret.)
SECRET_USES_CMK="no"
if [ -n "$SEC_ARN" ]; then
    skey="$(aws_ secretsmanager describe-secret --secret-id "$SECRET_NAME" --query "KmsKeyId" --output text 2>/dev/null || echo "")"
    [ "$skey" != "None" ] && [ -n "$skey" ] && [ "$(kms_used_by "$skey")" = "yes" ] && SECRET_USES_CMK="yes"
fi

# Is the CMK referenced by ANYTHING outside this stack? (Never delete a shared key.)
KMS_SHARED="no"
if [ -n "$KMS_ID" ]; then
    # Heuristic: grants whose grantee is a non-AWS-service principal, or aliases other
    # than ours, suggest external use. AWS service-principal grants (e.g. dynamodb.*)
    # are expected and not "external".
    other_aliases="$(aws_ kms list-aliases --key-id "$KMS_ID" \
        --query "Aliases[?AliasName!='${KMS_ALIAS}'].AliasName" --output text 2>/dev/null || echo "")"
    [ -n "$other_aliases" ] && KMS_SHARED="yes"
fi

# ----- SECRET fate (slaved to the MemoryDB cluster) ---------------------------
if [ -n "$SEC_ARN" ]; then
    if [ "$CHOICE_MEMORYDB" = "retain" ]; then
        CHOICE_SECRET="retain"
        SECRET_KEEP_REASONS+=("MemoryDB cluster is retained — the secret is required to authenticate on redeploy")
    fi
    if [ "$SECRET_USES_CMK" = "yes" ] && [ "$CHOICE_MEMORYDB" != "retain" ]; then
        # (Informational: if we keep the cluster's data via snapshot AND the secret is
        #  CMK-encrypted, the secret stays tied to the CMK — handled in KMS section.)
        :
    fi
    if [ "${#SECRET_KEEP_REASONS[@]}" -eq 0 ]; then
        # Cluster being deleted and secret not otherwise needed -> follow cluster intent.
        CHOICE_SECRET=$([ "$CHOICE_MEMORYDB" = "backup" ] && echo "backup" || echo "delete")
    else
        CHOICE_SECRET="retain"
    fi
fi

# ----- KMS fate (force-retain if ANY kept resource / backup needs it) ----------
if [ -n "$KMS_ID" ]; then
    [ "$CHOICE_TABLES" = "retain" ] && [ "$DDB_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("retained DynamoDB tables are encrypted with this key")
    [ "$CHOICE_TABLES" = "backup" ] && [ "$DDB_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("the DynamoDB backup being created inherits this key and a restore requires it")
    [ -n "$EXISTING_DDB_BACKUPS" ] && [ "$DDB_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("existing DynamoDB backup(s) require this key to restore")
    [ "$CHOICE_MEMORYDB" = "retain" ] && [ "$MDB_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("retained MemoryDB cluster is encrypted with this key")
    [ "$MDB_SNAP_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("existing MemoryDB snapshot(s) require this key to restore")
    [ "$CHOICE_MEMORYDB" = "backup" ] && [ "$MDB_USES_CMK" = "yes" ] && \
        KMS_KEEP_REASONS+=("the MemoryDB final snapshot being created uses this key")
    [ "$SECRET_USES_CMK" = "yes" ] && [ "$CHOICE_SECRET" != "delete" ] && \
        KMS_KEEP_REASONS+=("the retained/recoverable secret is encrypted with this key")
    [ "$KMS_SHARED" = "yes" ] && \
        KMS_KEEP_REASONS+=("the key has additional aliases — it may be shared with resources outside this stack")

    if [ "${#KMS_KEEP_REASONS[@]}" -gt 0 ]; then
        CHOICE_KMS="retain"
    else
        CHOICE_KMS="delete"   # nothing kept needs it
    fi
fi

# Summary of resolved plan
hr
echo "  ${BOLD}Resolved teardown plan:${NC}"
echo "    MemoryDB cluster : $CHOICE_MEMORYDB"
echo "    DynamoDB tables  : $CHOICE_TABLES"
[ -n "$KMS_ID" ]  && echo "    KMS key          : ${BOLD}$CHOICE_KMS${NC}"
[ -n "$SEC_ARN" ] && echo "    Secret           : ${BOLD}$CHOICE_SECRET${NC}"
echo "    Lambda layer     : $CHOICE_LAYER"
echo "    Monitoring stack : $CHOICE_MON"
echo "    Main stack       : $( [ "$CHOICE_MEMORYDB" = retain ] && echo 'retain MemoryDB sub-tree, delete the rest' || echo 'full delete' )"
hr

# Dependency notice — surface WHY protected resources are kept, so the user can
# decide to remove them manually later, themselves.
if [ "${#KMS_KEEP_REASONS[@]}" -gt 0 ] || [ "${#SECRET_KEEP_REASONS[@]}" -gt 0 ] \
   || [ -n "$EXISTING_DDB_BACKUPS" ] || [ -n "$EXISTING_MDB_SNAPSHOTS" ]; then
    echo "  ${BOLD}${YEL}Protected by dependency (kept to avoid data loss):${NC}"
    if [ "${#KMS_KEEP_REASONS[@]}" -gt 0 ]; then
        echo "    • KMS key ${KMS_ALIAS} is RETAINED because:"
        for r in "${KMS_KEEP_REASONS[@]}"; do echo "        - $r"; done
    fi
    if [ "${#SECRET_KEEP_REASONS[@]}" -gt 0 ]; then
        echo "    • Secret ${SECRET_NAME} is RETAINED because:"
        for r in "${SECRET_KEEP_REASONS[@]}"; do echo "        - $r"; done
    fi
    if [ -n "$EXISTING_DDB_BACKUPS" ]; then
        echo "    • Pre-existing DynamoDB backups (left untouched):"
        for a in $EXISTING_DDB_BACKUPS; do echo "        - $a"; done
    fi
    if [ -n "$EXISTING_MDB_SNAPSHOTS" ]; then
        echo "    • Pre-existing MemoryDB snapshots (left untouched):"
        for s in $EXISTING_MDB_SNAPSHOTS; do echo "        - $s"; done
    fi
    echo "    ${BLU}These are kept on purpose. Delete them yourself, manually, once you no"
    echo "    longer need the associated data/backups.${NC}"
    hr
fi
echo ""

# ==================================================================================
# STEP 1 — KICK OFF MEMORYDB DELETE (async) so it drains during the rest of teardown
# ==================================================================================
MDB_DELETE_STARTED=false
if [ -n "$CL_STATUS" ] && [ "$CHOICE_MEMORYDB" != "retain" ]; then
    echo "${BOLD}STEP 1 — Start MemoryDB cluster deletion (async; runs in background)${NC}"
    if [ "$CL_STATUS" = "deleting" ]; then
        info "Cluster already in 'deleting' state — will just wait for it later."
        MDB_DELETE_STARTED=true
    elif [ "$CHOICE_MEMORYDB" = "backup" ]; then
        # MemoryDB snapshot names: 1-40 chars, must begin with a letter, no consecutive
        # or trailing hyphens. "{prefix}-final-{stamp}" overflows 40 chars, so use a
        # compact name: drop the long prefix, keep env + a short stamp (mmdd-HHMM).
        SHORT_STAMP="$(date -u +%m%d-%H%M 2>/dev/null || echo manual)"
        SNAP_NAME="gsl-${ENVIRONMENT}-final-${SHORT_STAMP}"   # e.g. gsl-dev-final-0609-0401 (24 chars)
        SNAP_NAME="$(printf '%s' "$SNAP_NAME" | cut -c1-40)"
        run "delete MemoryDB cluster WITH final snapshot '$SNAP_NAME'" \
            aws_ memorydb delete-cluster --cluster-name "$CLUSTER_NAME" --final-snapshot-name "$SNAP_NAME" >/dev/null \
            && { MDB_DELETE_STARTED=true; [ "$EXECUTE" = true ] && ok "Snapshot '$SNAP_NAME' will be created, then cluster deleted."; }
    else
        run "delete MemoryDB cluster (no snapshot)" \
            aws_ memorydb delete-cluster --cluster-name "$CLUSTER_NAME" >/dev/null \
            && MDB_DELETE_STARTED=true
    fi
    echo ""
fi

# ==================================================================================
# STEP 2 — MEANWHILE: boto3 side-effects, DynamoDB backups, monitoring stack
# ==================================================================================
echo "${BOLD}STEP 2 — Cleanup that runs while MemoryDB drains${NC}"

# 2a. Provisioned concurrency + application-autoscaling (boto3-created; CFN-invisible)
info "Removing provisioned-concurrency / autoscaling (post-deploy boto3 side-effects)..."
for fn in "${PC_FUNCTIONS[@]}"; do
    # Deregister any autoscaling scalable targets for this function's versions.
    targets="$(aws_ application-autoscaling describe-scalable-targets \
        --service-namespace lambda \
        --query "ScalableTargets[?starts_with(ResourceId, 'function:${fn}:')].ResourceId" \
        --output text 2>/dev/null || echo "")"
    for rid in $targets; do
        run "deregister autoscaling target $rid" \
            aws_ application-autoscaling deregister-scalable-target \
            --service-namespace lambda --resource-id "$rid" \
            --scalable-dimension lambda:function:ProvisionedConcurrency >/dev/null || true
    done
    # Delete provisioned-concurrency configs across all versions of the function.
    pcs="$(aws_ lambda list-provisioned-concurrency-configs --function-name "$fn" \
        --query "ProvisionedConcurrencyConfigs[].FunctionArn" --output text 2>/dev/null || echo "")"
    for arn in $pcs; do
        qual="${arn##*:}"
        run "delete provisioned-concurrency for ${fn}:${qual}" \
            aws_ lambda delete-provisioned-concurrency-config --function-name "$fn" --qualifier "$qual" >/dev/null || true
    done
done

# 2b. DynamoDB backups (if chosen). Create BEFORE any table delete; confirm success.
DDB_BACKUP_ARNS=()
if [ "$CHOICE_TABLES" = "backup" ]; then
    info "Creating on-demand DynamoDB backups (persist after table deletion)..."
    for t in "$CONFIG_TABLE" "$STATS_TABLE"; do
        [ -n "$(exists_table "$t")" ] || { warn "Table $t absent — skipping backup."; continue; }
        bname="${t}-teardown-${STAMP}"
        if [ "$EXECUTE" = true ]; then
            info "RUN: create-backup $t -> $bname"
            arn="$(aws_ dynamodb create-backup --table-name "$t" --backup-name "$bname" \
                --query "BackupDetails.BackupArn" --output text)"
            [ -n "$arn" ] && [ "$arn" != "None" ] && { DDB_BACKUP_ARNS+=("$arn"); ok "Backup created: $arn"; } \
                || { err "Backup of $t did NOT confirm — aborting before deleting any table."; exit 1; }
        else
            echo "   ${YEL}[dry-run]${NC} would: create-backup $t -> $bname (and verify before any delete)"
        fi
    done
fi

# 2c. Monitoring stack
if [ "$CHOICE_MON" = "delete" ] && [ "$MON_STATUS" != "DOES_NOT_EXIST" ]; then
    if [ "$CDK_AVAILABLE" = true ]; then
        run "cdk destroy $MONITORING_STACK" \
            cdk destroy "$MONITORING_STACK" --app "$PYTHON_CMD app_post_deploy.py" \
            --context environment="$ENVIRONMENT" --context base_stack_name="$BASE_STACK" --force \
            </dev/null || warn "Monitoring stack destroy reported an error (continuing)."
    else
        run "delete monitoring stack via CloudFormation" \
            aws_ cloudformation delete-stack --stack-name "$MONITORING_STACK" || true
    fi
fi
echo ""

# ==================================================================================
# STEP 3 — REJOIN: wait for the MemoryDB cluster to be fully gone (+ snapshot ready)
# ==================================================================================
if [ "$MDB_DELETE_STARTED" = true ]; then
    echo "${BOLD}STEP 3 — Wait for MemoryDB cluster to finish deleting${NC}"
    if [ "$EXECUTE" = true ]; then
        # If a final snapshot was requested, it must reach 'available' before the
        # cluster disappears — the cluster status reflects 'snapshotting' first.
        WAITED=0; MAX=1800; STEP=30
        while [ "$WAITED" -lt "$MAX" ]; do
            st="$(exists_cluster)"
            if [ -z "$st" ]; then ok "Cluster $CLUSTER_NAME fully deleted (after ${WAITED}s)."; break; fi
            info "  cluster status: $st  (${WAITED}s elapsed)"; sleep "$STEP"; WAITED=$((WAITED+STEP))
        done
        [ -n "$(exists_cluster)" ] && warn "Cluster still present after ${MAX}s; proceeding (CFN/orphan sweep will handle dependents)."
        if [ "$CHOICE_MEMORYDB" = "backup" ]; then
            snapst="$(aws_ memorydb describe-snapshots --snapshot-name "$SNAP_NAME" --query "Snapshots[0].Status" --output text 2>/dev/null || echo "")"
            [ -n "$snapst" ] && ok "Final snapshot '$SNAP_NAME' status: $snapst" || warn "Could not confirm snapshot '$SNAP_NAME' — check the MemoryDB console."
        fi
    else
        echo "   ${YEL}[dry-run]${NC} would: poll describe-clusters until $CLUSTER_NAME is gone (up to 30 min), then verify snapshot."
    fi
    echo ""
fi

# ==================================================================================
# STEP 4 — DELETE THE MAIN STACK
# ==================================================================================
echo "${BOLD}STEP 4 — Delete the main stack ($BASE_STACK)${NC}"
if [ "$MAIN_STATUS" = "DOES_NOT_EXIST" ]; then
    info "Main stack does not exist — skipping stack delete (orphan sweep still runs)."
elif [ "$CDK_AVAILABLE" != true ]; then
    warn "cdk not available — cannot run a managed stack delete. Falling back to CloudFormation delete-stack."
    run "CloudFormation delete-stack $BASE_STACK" aws_ cloudformation delete-stack --stack-name "$BASE_STACK" || true
elif [ "$CHOICE_MEMORYDB" = "retain" ]; then
    # Retain the MemoryDB sub-tree: tell CloudFormation to retain those logical
    # resources so a stack delete keeps the cluster/groups/user/ACL intact.
    warn "MemoryDB retained: deleting the main stack while RETAINING the MemoryDB sub-tree."
    run "CloudFormation delete-stack $BASE_STACK retaining MemoryDB resources" \
        aws_ cloudformation delete-stack --stack-name "$BASE_STACK" \
        --retain-resources gamestatsleaderboardsdevmemorydb gamestatsleaderboardsdevmemorydbacl \
                           gamestatsleaderboardsdevmemorydbuser gamestatsleaderboardsdevmemorydbsubnetgroup \
                           gamestatsleaderboardsdevmemorydbparams || \
        warn "Retain-resources delete needs the resources to be in DELETE_FAILED first; if it errored, re-run after the stack reaches that state, or retain via the console."
else
    run "cdk destroy $BASE_STACK" \
        cdk destroy "$BASE_STACK" --app "$PYTHON_CMD app.py" \
        --context environment="$ENVIRONMENT" --force </dev/null \
        || warn "Main stack destroy reported an error — the orphan sweep below will report/clean leftovers."
fi
echo ""

# ==================================================================================
# STEP 5 — SWEEP RETAINED RESOURCES + ANY MEMORYDB ORPHANS (honoring each choice)
# ==================================================================================
echo "${BOLD}STEP 5 — Sweep retained resources & MemoryDB orphans${NC}"

# 5a. DynamoDB tables
if [ "$CHOICE_TABLES" = "retain" ]; then
    info "DynamoDB tables RETAINED ($CONFIG_TABLE, $STATS_TABLE) — left in place."
else
    for t in "$CONFIG_TABLE" "$STATS_TABLE"; do
        [ -n "$(exists_table "$t")" ] || { info "Table $t already gone."; continue; }
        # Disable deletion protection if enabled (ignore errors), then delete.
        run "disable deletion protection on $t (if set)" \
            aws_ dynamodb update-table --table-name "$t" --no-deletion-protection-enabled >/dev/null 2>&1 || true
        run "delete DynamoDB table $t" aws_ dynamodb delete-table --table-name "$t" >/dev/null || true
    done
fi

# 5b. KMS key — only reached when CHOICE_KMS=delete (tables deleted, no backup, none
#     pre-existing). KMS cannot be deleted instantly; schedule with a 7-day recovery
#     window (the minimum) and free the alias so a redeploy can recreate the key.
if [ -n "$KMS_ID" ] && [ "$CHOICE_KMS" = "delete" ]; then
    run "delete KMS alias $KMS_ALIAS (frees it for redeploy)" \
        aws_ kms delete-alias --alias-name "$KMS_ALIAS" >/dev/null 2>&1 || true
    run "schedule KMS key $KMS_ID deletion (7-day recovery window)" \
        aws_ kms schedule-key-deletion --key-id "$KMS_ID" --pending-window-in-days 7 >/dev/null || true
    [ "$EXECUTE" = true ] && warn "KMS key SCHEDULED for deletion in 7 days (recoverable until then via 'aws kms cancel-key-deletion')."
elif [ -n "$KMS_ID" ]; then
    info "KMS key RETAINED ($KMS_ALIAS)."
fi

# 5c. Secret
if [ -n "$SEC_ARN" ] && [ "$CHOICE_SECRET" != "retain" ]; then
    if [ "$CHOICE_SECRET" = "delete" ]; then
        run "force-delete secret $SECRET_NAME (no recovery)" \
            aws_ secretsmanager delete-secret --secret-id "$SECRET_NAME" --force-delete-without-recovery >/dev/null || true
    else
        run "schedule-delete secret $SECRET_NAME (30-day recovery window)" \
            aws_ secretsmanager delete-secret --secret-id "$SECRET_NAME" --recovery-window-in-days 30 >/dev/null || true
    fi
elif [ -n "$SEC_ARN" ]; then
    info "Secret RETAINED ($SECRET_NAME)."
fi

# 5d. Lambda layer versions
if [ -n "$LYR_VERS" ] && [ "$CHOICE_LAYER" = "delete" ]; then
    for v in $LYR_VERS; do
        run "delete Lambda layer version ${LAYER_NAME}:${v}" \
            aws_ lambda delete-layer-version --layer-name "$LAYER_NAME" --version-number "$v" >/dev/null || true
    done
elif [ -n "$LYR_VERS" ]; then
    info "Lambda layer RETAINED ($LAYER_NAME)."
fi

# 5e. MemoryDB orphans — only if NOT retaining the cluster. Belt-and-suspenders;
#     order matters: ACL -> User -> SubnetGroup/ParameterGroup (after cluster gone).
if [ "$CHOICE_MEMORYDB" != "retain" ]; then
    if [ -n "$(aws_ memorydb describe-acls --acl-name "$ACL_NAME" --query 'ACLs[0].Name' --output text 2>/dev/null || echo '')" ]; then
        run "delete MemoryDB ACL $ACL_NAME" aws_ memorydb delete-acl --acl-name "$ACL_NAME" >/dev/null || true
    fi
    if [ -n "$(aws_ memorydb describe-users --user-name "$MDB_USER" --query 'Users[0].Name' --output text 2>/dev/null || echo '')" ]; then
        run "delete MemoryDB user $MDB_USER" aws_ memorydb delete-user --user-name "$MDB_USER" >/dev/null || true
    fi
    if [ -n "$(aws_ memorydb describe-subnet-groups --subnet-group-name "$SUBNET_GROUP" --query 'SubnetGroups[0].Name' --output text 2>/dev/null || echo '')" ]; then
        run "delete MemoryDB subnet group $SUBNET_GROUP" aws_ memorydb delete-subnet-group --subnet-group-name "$SUBNET_GROUP" >/dev/null || true
    fi
    if [ -n "$(aws_ memorydb describe-parameter-groups --parameter-group-name "$PARAM_GROUP" --query 'ParameterGroups[0].Name' --output text 2>/dev/null || echo '')" ]; then
        run "delete MemoryDB parameter group $PARAM_GROUP" aws_ memorydb delete-parameter-group --parameter-group-name "$PARAM_GROUP" >/dev/null || true
    fi
else
    info "MemoryDB sub-tree RETAINED (cluster, ACL, user, subnet group, parameter group)."
fi
echo ""

# ==================================================================================
# STEP 6 — VERIFY + REPORT
# ==================================================================================
echo "${BOLD}STEP 6 — Verification & summary${NC}"
hr
echo "  Final stack status : $(stack_status "$BASE_STACK")  (main)   |   $(stack_status "$MONITORING_STACK")  (monitoring)"
echo "  MemoryDB cluster   : $( [ -n "$(exists_cluster)" ] && echo "${YEL}still present: $(exists_cluster)${NC}" || echo "${GRN}gone${NC}" )"
echo "  DynamoDB config    : $( [ -n "$(exists_table "$CONFIG_TABLE")" ] && echo "present ($CHOICE_TABLES)" || echo gone )"
echo "  DynamoDB stats     : $( [ -n "$(exists_table "$STATS_TABLE")" ] && echo "present ($CHOICE_TABLES)" || echo gone )"
echo "  KMS key            : $( [ -n "$(exists_kms)" ] && echo "present/scheduled ($CHOICE_KMS)" || echo gone )"
echo "  Secret             : $( [ -n "$(exists_secret)" ] && echo "present ($CHOICE_SECRET)" || echo gone )"
echo "  Lambda layer       : $( [ -n "$(layer_versions)" ] && echo "present ($CHOICE_LAYER)" || echo gone )"
hr
if [ "${#DDB_BACKUP_ARNS[@]}" -gt 0 ]; then
    echo "  ${BOLD}DynamoDB backups created (recovery handles):${NC}"
    for a in "${DDB_BACKUP_ARNS[@]}"; do echo "    • $a"; done
fi
if [ "$CHOICE_MEMORYDB" = "backup" ] && [ "$MDB_DELETE_STARTED" = true ]; then
    echo "  ${BOLD}MemoryDB final snapshot:${NC} ${SNAP_NAME:-<dry-run>}"
fi
hr
if [ "$EXECUTE" = true ]; then
    ok "Teardown complete. Review any ${YEL}WARN${NC} lines above for items needing a follow-up."
    info "Retained data resources (if any) will be correctly REUSED on a fresh redeploy."
else
    info "Dry-run finished — no changes made. Re-run with ${BOLD}--execute${NC} to perform the teardown."
fi
