use cached::proc_macro::{cached, once};
use lambda_http::{Body, Error, Request, RequestExt, Response};
use metrics_cloudwatch_embedded::lambda::handler::run_http;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tracing::{debug, info, info_span};

/// Input Jwt token claims
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
struct ClaimsIn {
    sub: String,
    iss: String,
    kid: String,
    aud: String,
    scope: String,
    access_token_scope: Option<String>,
    iat: i64,
    nbf: i64,
    exp: i64,
}

// Output Jwt token claims with references to save some allocations
#[derive(Debug, Serialize)]
struct ClaimsOut<'a> {
    sub: &'a str,
    iss: &'a str,
    kid: &'a str,
    aud: &'a str,
    scope: &'a str,
    access_token_scope: Option<&'a str>,
    iat: i64,
    nbf: i64,
    exp: i64,
}

/// Json body of success responses
#[derive(Debug, Serialize)]
struct ResponsePayload<'a> {
    user_id: &'a str,
    auth_token: &'a str,
    refresh_token: &'a str,
    auth_token_expires_in: i64,
    refresh_token_expires_in: i64,
}

fn generate_response(code: u16, body: &str) -> Response<Body> {
    Response::builder()
        .status(code)
        .header("Access-Control-Allow-Origin", "*")
        .header("Access-Control-Allow-Credentials", "true")
        .body(body.into())
        .expect("failed to generate response")
}

#[cached]
/// get our (cached) aws configuration
async fn get_aws_config() -> Arc<aws_config::SdkConfig> {
    Arc::new(aws_config::load_from_env().await)
}

#[cached(time = 900)]
/// get our private kid and key from secrects manager, panic on failure
async fn get_private_key() -> (Arc<String>, Arc<jsonwebtoken::EncodingKey>) {
    info!("refreshing private key from Secrets Manager");

    let aws_config = get_aws_config().await;
    let secrets_client = aws_sdk_secretsmanager::Client::new(&aws_config);

    let jwk: jsonwebkey::JsonWebKey = secrets_client
        .get_secret_value()
        .secret_id(std::env::var("SECRET_KEY_ID").unwrap())
        .send()
        .await
        .expect("failed to get SECRET_KEY_ID")
        .secret_string()
        .expect("SECRET_KEY_ID is blank")
        .to_string()
        .parse()
        .expect("private key is not a valid jwk");

    (
        Arc::new(jwk.key_id.unwrap()),
        Arc::new(jsonwebtoken::EncodingKey::from_rsa_pem(jwk.key.to_pem().as_bytes()).unwrap()),
    )
}

#[once(time = 900)]
/// get the json web keyset for our issuer, panic on failure
async fn get_keyset(issuer: &str) -> Arc<HashMap<String, jsonwebtoken::DecodingKey>> {
    info!("Refreshing json web keyset");

    use reqwest_retry::{policies::ExponentialBackoff, RetryTransientMiddleware};

    let retry_policy = ExponentialBackoff::builder().build_with_max_retries(3);
    let client = reqwest_middleware::ClientBuilder::new(reqwest::Client::new())
        .with(RetryTransientMiddleware::new_with_policy(retry_policy))
        .build();

    let jwks = client
        .get(format!("{issuer}/.well-known/jwks.json"))
        .send()
        .await
        .unwrap()
        .json::<jsonwebtoken::jwk::JwkSet>()
        .await
        .unwrap();

    let mut dict = HashMap::new();
    for jwk in jwks.keys {
        if let (Some(key_id), jsonwebtoken::jwk::AlgorithmParameters::RSA(rsa)) =
            (jwk.common.key_id, &jwk.algorithm)
        {
            dict.insert(
                key_id,
                jsonwebtoken::DecodingKey::from_rsa_components(&rsa.n, &rsa.e).unwrap(),
            );
        }
    }

    if dict.is_empty() {
        panic!("jwks has no valid keys");
    }

    Arc::new(dict)
}

async fn process_token(issuer: &str, refresh_token: &str) -> Result<Response<Body>, Error> {
    let header = jsonwebtoken::decode_header(refresh_token)?;
    let kid = header.kid.ok_or("kid missing from jwt header")?;

    let jks = get_keyset(issuer).await;
    let public_key = jks.get(&kid).ok_or("kid not in jks")?;

    let mut validation = jsonwebtoken::Validation::new(jsonwebtoken::Algorithm::RS256);
    validation.set_audience(&["refresh"]);
    validation.set_issuer(&[issuer]);

    let jwt = jsonwebtoken::decode::<ClaimsIn>(refresh_token, public_key, &validation)?;
    debug!("jwt = {jwt:?}");

    let user_id = jwt.claims.sub.as_str();
    let access_token_scope = &jwt
        .claims
        .access_token_scope
        .ok_or("missing access_token_scope claim")?;
    let access_token_duration_sec = 15 * 60;
    let existing_exp_value = jwt.claims.exp;

    let (private_kid, private_key) = get_private_key().await;

    // Build a new header with the latest kid
    let mut new_header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::RS256);
    new_header.kid = Some(private_kid.to_string());

    let now = time::OffsetDateTime::now_utc().unix_timestamp();

    // Build a new refresh token
    let refresh_claims = ClaimsOut {
        sub: user_id,
        iss: issuer,
        kid: &private_kid,
        aud: "refresh",
        scope: "refresh",
        access_token_scope: Some(access_token_scope),
        iat: now,
        nbf: now,
        exp: existing_exp_value,
    };
    let refresh_token = jsonwebtoken::encode(&new_header, &refresh_claims, &private_key)?;

    // Build a new access token
    let access_claims = ClaimsOut {
        sub: user_id,
        iss: issuer,
        kid: &private_kid,
        aud: "gamebackend",
        scope: access_token_scope,
        access_token_scope: None,
        iat: now,
        nbf: now,
        exp: now + access_token_duration_sec,
    };
    let access_token = jsonwebtoken::encode(&new_header, &access_claims, &private_key)?;

    let response_payload = ResponsePayload {
        user_id,
        auth_token: &access_token,
        auth_token_expires_in: access_token_duration_sec,
        refresh_token: &refresh_token,
        refresh_token_expires_in: existing_exp_value - now,
    };

    Ok(generate_response(
        200,
        &serde_json::to_string(&response_payload)?,
    ))
}

async fn function_handler(issuer: &str, request: Request) -> Result<Response<Body>, Error> {
    // Get the refresh_token from the query string
    let query = request.query_string_parameters();
    let refresh_token = query.first("refresh_token");

    match refresh_token {
        None => {
            metrics::increment_counter!("deny", "reason" => "No refresh token provided");
            Ok(generate_response(401, "Error: No refresh token provided"))
        }
        Some(refresh_token) => match process_token(issuer, refresh_token).await {
            Ok(response) => {
                metrics::increment_counter!("allow");
                Ok(response)
            }
            Err(e) => {
                // Record the details but don't give the remote client specifics
                metrics::increment_counter!("deny", "reason" => e.to_string());
                Ok(generate_response(
                    401,
                    "Error: Failed to validate refresh token",
                ))
            }
        },
    }
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::filter::EnvFilter::from_default_env())
        .with_target(false)
        .with_current_span(false)
        .without_time()
        .init();

    let issuer = std::env::var("ISSUER_URL").unwrap();

    let metrics = metrics_cloudwatch_embedded::Builder::new()
        .cloudwatch_namespace(std::env::var("POWERTOOLS_METRICS_NAMESPACE").unwrap())
        .with_dimension("service", std::env::var("POWERTOOLS_SERVICE_NAME").unwrap())
        .with_dimension(
            "function",
            std::env::var("AWS_LAMBDA_FUNCTION_NAME").unwrap(),
        )
        .lambda_cold_start_span(info_span!("cold start").entered())
        .lambda_cold_start_metric("ColdStart")
        .with_lambda_request_id("requestId")
        .init()
        .unwrap();

    run_http(metrics, |request: Request| {
        function_handler(&issuer, request)
    })
    .await
}
