#!/bin/bash
#
# ----------------------------------------------------------------
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# ----------------------------------------------------------------
#
# Enhanced Deployment Script for Game Stats & Leaderboards System
# Prepares the system environment and deploys the complete system

set -e

echo "🚀 Game Stats & Leaderboards System - Enhanced Deployment"
echo "=========================================================="

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to handle errors and prevent infinite loops
handle_error() {
    local exit_code=$?
    local line_number=$1
    print_error "Error occurred in script at line $line_number (exit code: $exit_code)"
    print_error "Last command that failed: $BASH_COMMAND"
    
    # Cleanup function
    cleanup_on_error() {
        print_status "Cleaning up temporary files..."
        cd "$SCRIPT_DIR" 2>/dev/null || true
        rm -rf /tmp/Python-* /tmp/awscliv2.zip /tmp/aws 2>/dev/null || true
    }
    
    cleanup_on_error
    exit $exit_code
}

# Set up error handling
trap 'handle_error $LINENO' ERR

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a Python package is installed
check_python_package() {
    local package=$1
    local min_version=$2
    local python_cmd=$3
    
    # Try to get package version
    local version=$($python_cmd -c "
try:
    import $package
    print(getattr($package, '__version__', 'unknown'))
except ImportError:
    print('not_installed')
" 2>/dev/null)
    
    if [ "$version" != "not_installed" ] && [ "$version" != "unknown" ]; then
        if [ -z "$min_version" ]; then
            print_success "Package $package version $version is already installed"
            return 0
        else
            # Simple version comparison (not perfect but works for most cases)
            if [ "$(printf '%s\n' "$min_version" "$version" | sort -V | head -n1)" = "$min_version" ]; then
                print_success "Package $package version $version is already installed (min: $min_version)"
                return 0
            else
                print_warning "Package $package version $version is installed but below minimum $min_version"
                return 1
            fi
        fi
    else
        print_status "Package $package is not installed"
        return 1
    fi
}

# Function to persist aliases and PATH across shell sessions
persist_environment() {
    local shell_rc=""
    local profile_file=""
    
    # Determine which shell configuration files to use
    if [ -n "$BASH_VERSION" ]; then
        shell_rc="$HOME/.bashrc"
        profile_file="$HOME/.bash_profile"
    elif [ -n "$ZSH_VERSION" ]; then
        shell_rc="$HOME/.zshrc"
        profile_file="$HOME/.zprofile"
    else
        # Default fallback
        shell_rc="$HOME/.bashrc"
        profile_file="$HOME/.profile"
    fi
    
    # Create files if they don't exist
    touch "$shell_rc" "$profile_file"
    
    print_status "Updating environment configuration in $shell_rc and $profile_file"
    
    # Add environment setup section
    local env_marker="# === Game Stats Deployment Environment Setup ==="
    
    # Remove existing section if present
    if grep -q "$env_marker" "$shell_rc"; then
        # Create temporary file without the old section
        awk "/$env_marker/,/$env_marker END/ {next} 1" "$shell_rc" > "${shell_rc}.tmp"
        mv "${shell_rc}.tmp" "$shell_rc"
    fi
    
    # Add new environment section to shell RC
    cat >> "$shell_rc" << EOF

$env_marker
# NVM Configuration
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

# Python and Pip PATH
export PATH="/usr/local/bin:$PATH"
export PATH="$HOME/.local/bin:$PATH"

# AWS CLI PATH
export PATH="$HOME/.local/bin:$PATH"

# Ensure latest pip is used
alias pip='python3 -m pip'
alias pip3='python3 -m pip'

# Python version aliases for compatibility (minimum 3.13 required)
if command -v python3.15 &> /dev/null; then
    alias python3='python3.15'
    alias pip3='python3.15 -m pip'
elif command -v python3.14 &> /dev/null; then
    alias python3='python3.14'
    alias pip3='python3.14 -m pip'
elif command -v python3.13 &> /dev/null; then
    alias python3='python3.13'
    alias pip3='python3.13 -m pip'
fi

# CDK and Node.js environment
if command -v node &> /dev/null; then
    export NODE_PATH="$(npm root -g)"
fi

# AWS CDK environment
export CDK_NEW_BOOTSTRAP=1
export CDK_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}

$env_marker END

EOF

    # Also add to profile file for login shells
    if ! grep -q "$env_marker" "$profile_file"; then
        cat >> "$profile_file" << EOF

$env_marker
# Source the main shell configuration
if [ -f "$shell_rc" ]; then
    source "$shell_rc"
fi
$env_marker END

EOF
    fi
    
    print_success "Environment configuration updated"
}

# Function to reload environment
reload_environment() {
    print_status "Reloading environment..."
    
    # Source the updated configuration
    if [ -f "$HOME/.bashrc" ]; then
        source "$HOME/.bashrc" 2>/dev/null || true
    fi
    if [ -f "$HOME/.zshrc" ]; then
        source "$HOME/.zshrc" 2>/dev/null || true
    fi
    if [ -f "$HOME/.profile" ]; then
        source "$HOME/.profile" 2>/dev/null || true
    fi
    
    # Reload NVM if available
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    print_success "Environment reloaded"
}

# Function to install AWS CLI v2
install_aws_cli() {
    print_status "Checking AWS CLI installation..."
    
    # Check if AWS CLI v2 is already installed
    if command -v aws &> /dev/null; then
        AWS_VERSION=$(aws --version 2>&1 | cut -d/ -f2 | cut -d' ' -f1)
        if [[ "$AWS_VERSION" == 2.* ]]; then
            print_success "AWS CLI v2 ($AWS_VERSION) is already installed"
            return 0
        else
            print_warning "AWS CLI v1 detected. Upgrading to v2..."
        fi
    fi
    
    print_status "Installing AWS CLI v2..."
    
    # Detect architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" == "x86_64" ]]; then
        AWS_ARCH="x86_64"
    elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
        AWS_ARCH="aarch64"
    else
        print_error "Unsupported architecture: $ARCH"
        exit 1
    fi
    
    # Download and install AWS CLI v2
    cd /tmp
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o "awscliv2.zip"
    
    if command -v unzip &> /dev/null; then
        unzip -q awscliv2.zip
    else
        print_error "unzip command not found. Please install unzip first."
        exit 1
    fi
    
    # Install AWS CLI
    if [ -d "/usr/local/aws-cli" ]; then
        sudo ./aws/install --update
    else
        sudo ./aws/install
    fi
    
    # Create symlink if needed
    if [ ! -L "/usr/local/bin/aws" ]; then
        sudo ln -sf /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws
    fi
    
    # Clean up
    rm -rf aws awscliv2.zip
    cd "$SCRIPT_DIR"
    
    # Verify installation
    if command -v aws &> /dev/null; then
        AWS_VERSION=$(aws --version 2>&1 | cut -d/ -f2 | cut -d' ' -f1)
        print_success "AWS CLI v2 ($AWS_VERSION) installed successfully"
    else
        print_error "AWS CLI installation failed"
        exit 1
    fi
}

# Function to upgrade pip to latest version
upgrade_pip() {
    local python_cmd=$1
    print_status "Upgrading pip to latest version for $python_cmd..."

    # Always use "$python_cmd -m pip" rather than a bare pip/pip3 command.
    # A bare pip3 on the system PATH may be tied to a different Python version
    # (or even Python 2 on older systems). Using -m pip guarantees we upgrade
    # the pip that belongs to the exact interpreter we selected.
    $python_cmd -m pip install --upgrade pip --user

    # Verify pip installation
    PIP_VERSION=$($python_cmd -m pip --version | cut -d' ' -f2)
    print_success "pip upgraded to version $PIP_VERSION"

    # Also install wheel and setuptools
    print_status "Installing essential Python packages..."
    $python_cmd -m pip install --upgrade wheel setuptools --user

    print_success "Essential Python packages installed"
}

# =============================================
# SYSTEM PREPARATION SECTION
# =============================================

print_status "Starting system preparation..."

# Check OS and distribution
print_status "Detecting OS and distribution..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
    print_status "Detected OS: $OS $VER"
elif type lsb_release >/dev/null 2>&1; then
    OS=$(lsb_release -si)
    VER=$(lsb_release -sr)
    print_status "Detected OS: $OS $VER"
elif [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
    OS=$DISTRIB_ID
    VER=$DISTRIB_RELEASE
    print_status "Detected OS: $OS $VER"
elif [ -f /etc/debian_version ]; then
    OS=Debian
    VER=$(cat /etc/debian_version)
    print_status "Detected OS: $OS $VER"
elif [ -f /etc/redhat-release ]; then
    OS=RedHat
    VER=$(cat /etc/redhat-release | cut -d ' ' -f 7)
    print_status "Detected OS: $OS $VER"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
    VER=$(sw_vers -productVersion)
    print_status "Detected OS: $OS $VER"
else
    OS=$(uname -s)
    VER=$(uname -r)
    print_status "Detected OS: $OS $VER"
fi

# Update system packages based on OS
print_status "Checking system packages..."
if [[ "$OS" == *"Amazon Linux"* ]] || [[ "$OS" == *"RedHat"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Fedora"* ]]; then
    print_status "Using yum/dnf package manager..."
    
    # Determine package manager
    if command -v dnf &> /dev/null; then
        PKG_MGR="dnf"
    else
        PKG_MGR="yum"
    fi
    
    # Special handling for Amazon Linux 2023
    if [[ "$OS" == *"Amazon Linux"* ]] && [[ "$VER" == *"2023"* ]]; then
        print_status "Detected Amazon Linux 2023 - using optimized package installation"
        
        # Update system first (excluding curl to avoid conflicts)
        print_status "Updating system packages (excluding curl)..."
        sudo $PKG_MGR update -y --exclude=curl\*
        
        # Install packages one by one to handle conflicts better
        REQUIRED_PACKAGES=("gcc" "gcc-c++" "make" "git" "wget" "tar" "zip" "unzip" "which" "openssl-devel" "bzip2-devel" "libffi-devel" "xz-devel" "readline-devel" "sqlite-devel" "tk-devel" "gdbm-devel" "ncurses-devel")
        
        for pkg in "${REQUIRED_PACKAGES[@]}"; do
            if ! rpm -q "$pkg" &>/dev/null; then
                print_status "Installing $pkg..."
                if ! sudo $PKG_MGR install -y "$pkg"; then
                    print_warning "Failed to install $pkg, but continuing..."
                fi
            else
                print_success "$pkg is already installed"
            fi
        done
        
        # Ensure curl functionality is available
        if ! command -v curl &> /dev/null; then
            print_warning "curl command not available, but curl-minimal should provide basic functionality"
        else
            print_success "curl functionality is available"
        fi
        
    else
        # Standard installation for other RHEL-based systems
        PACKAGES=("gcc" "gcc-c++" "make" "git" "wget" "curl" "tar" "zip" "unzip" "which" "procps" "openssl-devel" "bzip2-devel" "libffi-devel" "xz-devel" "readline-devel" "sqlite-devel" "tk-devel" "gdbm-devel" "ncurses-devel")
        MISSING_PACKAGES=()
        
        # Check each package
        for pkg in "${PACKAGES[@]}"; do
            if ! rpm -q "$pkg" &>/dev/null; then
                MISSING_PACKAGES+=("$pkg")
            fi
        done
        
        # Install missing packages
        if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
            print_status "Some required packages are missing: ${MISSING_PACKAGES[*]}"
            print_status "Updating system packages..."
            sudo $PKG_MGR update -y
            
            print_status "Installing missing packages: ${MISSING_PACKAGES[*]}"
            sudo $PKG_MGR install -y "${MISSING_PACKAGES[@]}"
        else
            print_success "All required system packages are already installed"
        fi
    fi    
elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
    print_status "Using apt package manager..."
    
    # Define required packages
    PACKAGES=("build-essential" "git" "curl" "wget" "tar" "zip" "unzip" "software-properties-common" "apt-transport-https" "ca-certificates" "gnupg" "lsb-release" "libssl-dev" "libbz2-dev" "libffi-dev" "libreadline-dev" "libsqlite3-dev" "libncurses5-dev" "libncursesw5-dev" "xz-utils" "tk-dev" "libgdbm-dev" "libc6-dev" "libbz2-dev")
    MISSING_PACKAGES=()
    
    # Check each package
    for pkg in "${PACKAGES[@]}"; do
        if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            MISSING_PACKAGES+=("$pkg")
        fi
    done
    
    # Only update and install if packages are missing
    if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
        print_status "Some required packages are missing: ${MISSING_PACKAGES[*]}"
        print_status "Updating system packages..."
        sudo apt-get update
        
        print_status "Installing missing packages: ${MISSING_PACKAGES[*]}"
        sudo apt-get install -y "${MISSING_PACKAGES[@]}"
    else
        print_success "All required system packages are already installed"
    fi

elif [[ "$OS" == "macOS" ]]; then
    print_status "Detected macOS - checking for Homebrew..."
    
    # Check if Homebrew is installed
    if ! command -v brew &> /dev/null; then
        print_status "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        
        # Add Homebrew to PATH
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f "/usr/local/bin/brew" ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    else
        print_success "Homebrew is already installed"
    fi
    
    # Install required packages
    PACKAGES=("git" "wget" "curl" "openssl" "readline" "sqlite3" "xz" "zlib")
    print_status "Installing required packages via Homebrew..."
    brew install "${PACKAGES[@]}" || true
    
else
    print_warning "Unsupported OS for automatic updates: $OS"
    print_warning "Please update your system manually before continuing"
    read -p "Press Enter to continue or Ctrl+C to abort..."
fi

# Install AWS CLI v2
install_aws_cli

# Check if NVM is already installed
print_status "Checking for NVM installation..."
export NVM_DIR="$HOME/.nvm"
NVM_INSTALLED=false

if [ -d "$NVM_DIR" ] && [ -s "$NVM_DIR/nvm.sh" ]; then
    # Load NVM
    . "$NVM_DIR/nvm.sh"
    . "$NVM_DIR/bash_completion" 2>/dev/null || true
    
    if command -v nvm &> /dev/null; then
        NVM_VERSION=$(nvm --version 2>/dev/null)
        if [ -n "$NVM_VERSION" ]; then
            print_success "NVM version $NVM_VERSION is already installed"
            NVM_INSTALLED=true
        fi
    fi
fi

# Install NVM only if not already installed
if [ "$NVM_INSTALLED" != "true" ]; then
    print_status "Installing latest NVM (Node Version Manager)..."
    
    # Get the latest NVM version
    NVM_LATEST=$(curl -s https://api.github.com/repos/nvm-sh/nvm/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
    
    if [ -z "$NVM_LATEST" ]; then
        print_warning "Could not determine latest NVM version, using master branch"
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash
    else
        print_status "Installing NVM $NVM_LATEST"
        curl -o- "https://raw.githubusercontent.com/nvm-sh/nvm/$NVM_LATEST/install.sh" | bash
    fi
    
    # Load NVM
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    # Verify NVM installation
    if command -v nvm &> /dev/null; then
        NVM_VERSION=$(nvm --version)
        print_success "NVM installed successfully (version $NVM_VERSION)"
    else
        print_error "NVM installation failed. Please install NVM manually."
        print_error "Visit: https://github.com/nvm-sh/nvm#installing-and-updating"
        exit 1
    fi
fi

# Check if Node.js LTS is already installed
print_status "Checking Node.js installation..."
# Minimum Node.js major version. AWS CDK and current npm (npm@11 requires
# Node >=20.17) need an actively-supported runtime; Node 18 is end-of-life
# (since 2025-04) and is rejected here so the deploy uses an LTS line.
MIN_NODE_MAJOR=20
NODE_OK=false

if command -v node &> /dev/null; then
    NODE_VERSION=$(node -v)
    NPM_VERSION=$(npm -v)
    # Parse major version from "vXX.YY.ZZ"
    NODE_MAJOR=$(printf '%s' "$NODE_VERSION" | sed -E 's/^v?([0-9]+).*/\1/')

    if command -v nvm &> /dev/null \
       && nvm list 2>/dev/null | grep -q "$(node -v)" \
       && [ "${NODE_MAJOR:-0}" -ge "$MIN_NODE_MAJOR" ] 2>/dev/null; then
        print_success "Node.js $NODE_VERSION with npm $NPM_VERSION is already installed via NVM"
        NODE_OK=true
    elif [ "${NODE_MAJOR:-0}" -lt "$MIN_NODE_MAJOR" ] 2>/dev/null; then
        print_warning "Node.js $NODE_VERSION is older than required v${MIN_NODE_MAJOR}.x (and may be end-of-life)"
        print_status "Installing Node.js LTS via NVM..."
    else
        print_warning "Node.js $NODE_VERSION is installed but not managed by NVM"
        print_status "Installing Node.js LTS via NVM for consistency..."
    fi
fi

# Install Node.js LTS if the current runtime is missing, unmanaged, or too old
if [ "$NODE_OK" != "true" ]; then
    print_status "Installing latest Node.js LTS version..."
    nvm install --lts
    nvm use --lts
    nvm alias default 'lts/*'

    # Verify Node.js installation and that it now meets the minimum
    if command -v node &> /dev/null; then
        NODE_VERSION=$(node -v)
        NPM_VERSION=$(npm -v)
        NODE_MAJOR=$(printf '%s' "$NODE_VERSION" | sed -E 's/^v?([0-9]+).*/\1/')
        if [ "${NODE_MAJOR:-0}" -lt "$MIN_NODE_MAJOR" ] 2>/dev/null; then
            print_error "Installed Node.js $NODE_VERSION is still below required v${MIN_NODE_MAJOR}.x"
            exit 1
        fi
        print_success "Node.js $NODE_VERSION with npm $NPM_VERSION installed successfully"
    else
        print_error "Node.js installation failed"
        exit 1
    fi
fi

# Upgrade npm to the latest version COMPATIBLE with the active Node.js.
# Using "npm@latest" can pull a release whose engines field excludes the
# current Node (e.g. npm@11 needs Node >=20.17), which fails with EBADENGINE.
# Let npm pick a satisfying version itself; treat a failed upgrade as
# non-fatal since the bundled npm is sufficient for the rest of the deploy.
print_status "Upgrading npm to the latest version compatible with $(node -v)..."
if npm install -g npm@latest 2>/dev/null; then
    print_success "npm upgraded to version $(npm -v)"
else
    print_warning "Could not install npm@latest for $(node -v); keeping npm $(npm -v)"
    print_status "(This is non-fatal — the bundled npm works for this deploy.)"
fi
NPM_VERSION=$(npm -v)

# Show Node.js version information
print_status "Node.js version information:"
node -e "console.log('Architecture: ' + process.arch)"
node -e "console.log('Platform: ' + process.platform)"
node -e "console.log('Node.js version: ' + process.version)"
node -e "console.log('V8 version: ' + process.versions.v8)"
node -e "console.log('npm version: ' + process.versions.npm)"

# Check if AWS CDK is already installed and at the latest version
print_status "Checking AWS CDK installation..."
if command -v cdk &> /dev/null; then
    CDK_VERSION=$(cdk --version)
    print_success "AWS CDK is already installed: $CDK_VERSION"
    
    # Check for CDK updates
    print_status "Checking for CDK updates..."
    if npm outdated -g aws-cdk 2>/dev/null | grep -q aws-cdk; then
        print_status "Updating AWS CDK to the latest version..."
        npm update -g aws-cdk
        CDK_VERSION=$(cdk --version)
        print_success "AWS CDK updated to: $CDK_VERSION"
    else
        print_success "AWS CDK is already at the latest version"
    fi
else
    print_status "Installing AWS CDK globally..."
    npm install -g aws-cdk
    
    # Verify CDK installation
    if command -v cdk &> /dev/null; then
        CDK_VERSION=$(cdk --version)
        print_success "AWS CDK installed successfully: $CDK_VERSION"
    else
        print_error "AWS CDK installation failed"
        exit 1
    fi
fi

# Check for Python installation
print_status "Checking for Python installation..."
PYTHON_INSTALLED=false
PYTHON_CMD=""
PYTHON_MAJOR_MINOR=""

# Check for existing Python installations (prefer newer versions)
for py_ver in 3.15 3.14 3.13; do
    if command -v python$py_ver &> /dev/null; then
        PYTHON_CMD="python$py_ver"
        PYTHON_MAJOR_MINOR=$py_ver
        INSTALLED_VERSION=$(python$py_ver --version | cut -d' ' -f2)
        print_success "Python $INSTALLED_VERSION is already installed"
        PYTHON_INSTALLED=true
        break
    fi
done

# If no specific version found, check for python3
if [ "$PYTHON_INSTALLED" != "true" ] && command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    PYTHON_MAJOR_MINOR=$(echo $PYTHON_VERSION | cut -d. -f1,2)

    # Check if version is acceptable (3.13+)
    if [[ "$(printf '%s\n' "3.13" "$PYTHON_MAJOR_MINOR" | sort -V | head -n1)" == "3.13" ]]; then
        PYTHON_CMD="python3"
        print_success "Python $PYTHON_VERSION is already installed"
        PYTHON_INSTALLED=true
    else
        print_warning "Python $PYTHON_VERSION is too old (minimum 3.13 required)"
    fi
fi

# On some newer systems (Arch Linux, Fedora, minimal containers) Python 3 is
# available only as "python" with no "python3" symlink. Check for that case,
# but verify it is actually Python 3 (not Python 2).
if [ "$PYTHON_INSTALLED" != "true" ] && command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1 | cut -d' ' -f2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MAJOR_MINOR=$(echo $PYTHON_VERSION | cut -d. -f1,2)

    if [ "$PYTHON_MAJOR" = "3" ] && [[ "$(printf '%s\n' "3.13" "$PYTHON_MAJOR_MINOR" | sort -V | head -n1)" == "3.13" ]]; then
        PYTHON_CMD="python"
        print_success "Python $PYTHON_VERSION is already installed (as 'python')"
        PYTHON_INSTALLED=true
    elif [ "$PYTHON_MAJOR" = "2" ]; then
        print_warning "Found 'python' but it is Python $PYTHON_VERSION (Python 2) -- skipping"
    else
        print_warning "Found 'python' but version $PYTHON_VERSION is too old (minimum 3.13 required)"
    fi
fi

# Install Python if not already installed or too old
if [ "$PYTHON_INSTALLED" != "true" ]; then
    print_status "Installing Python..."
    
    # Get latest Python version
    LATEST_PYTHON_VERSION=$(curl -s https://www.python.org/ftp/python/ | 
                           grep -oE 'href="[0-9]+\.[0-9]+\.[0-9]+/"' | 
                           grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | 
                           sort -V | tail -1)
    
    if [ -z "$LATEST_PYTHON_VERSION" ]; then
        print_warning "Could not determine latest Python version. Using 3.13.0 as default."
        LATEST_PYTHON_VERSION="3.13.0"
    fi
    
    PYTHON_MAJOR_MINOR=$(echo $LATEST_PYTHON_VERSION | cut -d. -f1,2)
    PYTHON_CMD="python$PYTHON_MAJOR_MINOR"
    
    print_status "Installing Python $LATEST_PYTHON_VERSION..."
    
    if [[ "$OS" == *"Amazon Linux"* ]] || [[ "$OS" == *"RedHat"* ]] || [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Fedora"* ]]; then
        # For RHEL-based systems
        PKG_MGR="dnf"
        if ! command -v dnf &> /dev/null; then
            PKG_MGR="yum"
        fi
        
        # Try to install Python 3.13+ from OS repositories before falling back to source
        if [[ "$OS" == *"Amazon Linux"* ]] && [[ "$VER" == *"2023"* ]]; then
            # Amazon Linux 2023 -- try 3.13+ from repos (may not be available yet)
            for repo_ver in 3.14 3.13; do
                if sudo $PKG_MGR list python${repo_ver} &>/dev/null 2>&1; then
                    print_status "Installing Python ${repo_ver} from Amazon Linux 2023 repositories..."
                    sudo $PKG_MGR install -y python${repo_ver} python${repo_ver}-pip python${repo_ver}-devel
                    PYTHON_CMD=python${repo_ver}
                    PYTHON_MAJOR_MINOR=${repo_ver}
                    PYTHON_INSTALLED=true
                    break
                fi
            done
        elif [[ "$OS" == *"Fedora"* ]]; then
            # Fedora usually has recent Python versions
            for repo_ver in 3.14 3.13; do
                if sudo $PKG_MGR list python${repo_ver} &>/dev/null 2>&1; then
                    print_status "Installing Python ${repo_ver} from Fedora repositories..."
                    sudo $PKG_MGR install -y python${repo_ver} python${repo_ver}-pip python${repo_ver}-devel
                    PYTHON_CMD=python${repo_ver}
                    PYTHON_MAJOR_MINOR=${repo_ver}
                    PYTHON_INSTALLED=true
                    break
                fi
            done
        fi
        
        # If not installed from repos, compile from source
        if [ "$PYTHON_INSTALLED" != "true" ]; then
            print_status "Compiling Python $LATEST_PYTHON_VERSION from source..."
            
            # Download and install Python
            cd /tmp
            wget "https://www.python.org/ftp/python/$LATEST_PYTHON_VERSION/Python-$LATEST_PYTHON_VERSION.tgz"
            tar xzf "Python-$LATEST_PYTHON_VERSION.tgz"
            cd "Python-$LATEST_PYTHON_VERSION"
            
            # Configure with optimizations
            ./configure --enable-optimizations --with-ensurepip=install --prefix="/usr/local/python$PYTHON_MAJOR_MINOR"
            
            # Build (use all available cores)
            make -j $(nproc)
            
            # Install
            sudo make altinstall
            
            # Create symlinks
            sudo ln -sf "/usr/local/python$PYTHON_MAJOR_MINOR/bin/$PYTHON_CMD" "/usr/local/bin/$PYTHON_CMD"
            sudo ln -sf "/usr/local/python$PYTHON_MAJOR_MINOR/bin/pip$PYTHON_MAJOR_MINOR" "/usr/local/bin/pip$PYTHON_MAJOR_MINOR"
            
            cd "$SCRIPT_DIR"
            rm -rf "/tmp/Python-$LATEST_PYTHON_VERSION" "/tmp/Python-$LATEST_PYTHON_VERSION.tgz"
        fi
        
    elif [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        # For Debian-based systems
        # Try deadsnakes PPA for Ubuntu
        if [[ "$OS" == *"Ubuntu"* ]]; then
            print_status "Adding deadsnakes PPA for latest Python versions..."
            sudo add-apt-repository ppa:deadsnakes/ppa -y
            sudo apt-get update
            
            # Try to install Python 3.13+ from deadsnakes PPA
            for repo_ver in 3.14 3.13; do
                if apt-cache show python${repo_ver} &>/dev/null 2>&1; then
                    print_status "Installing Python ${repo_ver} from deadsnakes PPA..."
                    sudo apt-get install -y python${repo_ver} python${repo_ver}-venv python${repo_ver}-dev python${repo_ver}-distutils 2>/dev/null \
                        || sudo apt-get install -y python${repo_ver} python${repo_ver}-venv python${repo_ver}-dev
                    PYTHON_CMD=python${repo_ver}
                    PYTHON_MAJOR_MINOR=${repo_ver}
                    PYTHON_INSTALLED=true
                    break
                fi
            done
        fi
        
        # If not installed from repos, compile from source
        if [ "$PYTHON_INSTALLED" != "true" ]; then
            print_status "Compiling Python $LATEST_PYTHON_VERSION from source..."
            
            cd /tmp
            wget "https://www.python.org/ftp/python/$LATEST_PYTHON_VERSION/Python-$LATEST_PYTHON_VERSION.tgz"
            tar xzf "Python-$LATEST_PYTHON_VERSION.tgz"
            cd "Python-$LATEST_PYTHON_VERSION"
            
            ./configure --enable-optimizations --with-ensurepip=install --prefix="/usr/local/python$PYTHON_MAJOR_MINOR"
            make -j $(nproc)
            sudo make altinstall
            
            sudo ln -sf "/usr/local/python$PYTHON_MAJOR_MINOR/bin/$PYTHON_CMD" "/usr/local/bin/$PYTHON_CMD"
            sudo ln -sf "/usr/local/python$PYTHON_MAJOR_MINOR/bin/pip$PYTHON_MAJOR_MINOR" "/usr/local/bin/pip$PYTHON_MAJOR_MINOR"
            
            cd "$SCRIPT_DIR"
            rm -rf "/tmp/Python-$LATEST_PYTHON_VERSION" "/tmp/Python-$LATEST_PYTHON_VERSION.tgz"
        fi
        
    elif [[ "$OS" == "macOS" ]]; then
        # For macOS using Homebrew
        print_status "Installing Python 3.13+ via Homebrew..."
        brew install python@3.13 || brew install python@3.14 || brew install python

        # Find the installed Python (3.13+ only)
        for py_ver in 3.15 3.14 3.13; do
            if command -v python$py_ver &> /dev/null; then
                PYTHON_CMD="python$py_ver"
                PYTHON_MAJOR_MINOR=$py_ver
                PYTHON_INSTALLED=true
                break
            fi
        done
        
        if [ "$PYTHON_INSTALLED" != "true" ] && command -v python3 &> /dev/null; then
            PYTHON_CMD="python3"
            PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
            PYTHON_MAJOR_MINOR=$(echo $PYTHON_VERSION | cut -d. -f1,2)
            PYTHON_INSTALLED=true
        fi
    fi
fi

# Verify Python installation
if [ "$PYTHON_INSTALLED" != "true" ] || ! command -v $PYTHON_CMD &> /dev/null; then
    print_error "Python installation failed"
    exit 1
fi

INSTALLED_PYTHON_VERSION=$($PYTHON_CMD --version | cut -d' ' -f2)
print_success "Python $INSTALLED_PYTHON_VERSION is available as $PYTHON_CMD"

# Ensure pip is available before attempting to upgrade it.
# Some Python installations (e.g., source builds, minimal OS packages) may not
# include pip or ensurepip. We check first and bootstrap via get-pip.py if needed.
print_status "Checking if pip is available for $PYTHON_CMD..."
if ! $PYTHON_CMD -m pip --version &> /dev/null; then
    print_warning "pip is not available for $PYTHON_CMD -- bootstrapping now..."

    # Try ensurepip first (ships with most CPython builds that used --with-ensurepip)
    if $PYTHON_CMD -m ensurepip --upgrade --user 2>/dev/null; then
        print_success "pip bootstrapped via ensurepip"
    else
        # Fallback: download the official get-pip.py installer
        print_status "ensurepip not available -- installing pip via get-pip.py..."
        cd /tmp
        curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        $PYTHON_CMD get-pip.py --user
        rm -f get-pip.py
        cd "$SCRIPT_DIR"
    fi

    # On some distros pip lands in ~/.local/bin which may not be in PATH yet
    export PATH="$HOME/.local/bin:$PATH"

    # Verify the bootstrap succeeded before continuing
    if ! $PYTHON_CMD -m pip --version &> /dev/null; then
        print_error "Failed to bootstrap pip for $PYTHON_CMD"
        print_error "Please install pip manually (e.g., 'sudo apt install python3-pip' or 'sudo dnf install python3-pip') and re-run this script."
        exit 1
    fi
    print_success "pip bootstrapped successfully"
else
    print_success "pip is already available for $PYTHON_CMD"
fi

# Upgrade pip to latest version
upgrade_pip "$PYTHON_CMD"

# Verify pip is working
if $PYTHON_CMD -m pip --version &> /dev/null; then
    PIP_VERSION=$($PYTHON_CMD -m pip --version | cut -d' ' -f2)
    print_success "pip $PIP_VERSION is working correctly"
else
    print_error "pip installation failed"
    exit 1
fi

# Check if required Python packages are already installed
print_status "Checking required Python packages..."
PACKAGES_TO_INSTALL=()

# Check each required package
check_python_package "boto3" "" "$PYTHON_CMD" || PACKAGES_TO_INSTALL+=("boto3")
check_python_package "aws_cdk_lib" "" "$PYTHON_CMD" || PACKAGES_TO_INSTALL+=("aws-cdk-lib")
check_python_package "constructs" "" "$PYTHON_CMD" || PACKAGES_TO_INSTALL+=("constructs")
check_python_package "aws_lambda_powertools" "" "$PYTHON_CMD" || PACKAGES_TO_INSTALL+=("aws-lambda-powertools")

# Install only missing packages
if [ ${#PACKAGES_TO_INSTALL[@]} -gt 0 ]; then
    print_status "Installing missing Python packages: ${PACKAGES_TO_INSTALL[*]}"
    $PYTHON_CMD -m pip install "${PACKAGES_TO_INSTALL[@]}" --user || {
        print_warning "Failed to install some Python packages. Will try again during deployment."
    }
else
    print_success "All required Python packages are already installed"
fi

# Update environment configuration
persist_environment

# Reload environment to make everything available
reload_environment

print_success "System preparation completed successfully!"
echo ""

# =============================================
# DEPLOYMENT SECTION
# =============================================

print_status "Starting deployment phase..."

# Check prerequisites
print_status "Checking prerequisites..."

# Ensure we have the right Python command
if ! command -v $PYTHON_CMD &> /dev/null; then
    # Try to find Python again after environment reload, add newer versions to this if your system may have multiple versions. Consider reordering, if you need a specific version to run...
    for py_ver in 3.15 3.14 3.13; do
        if command -v python$py_ver &> /dev/null; then
            PYTHON_CMD="python$py_ver"
            break
        fi
    done
    
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    fi

    # Also check bare "python" (may be Python 3 on some systems)
    if ! command -v $PYTHON_CMD &> /dev/null && command -v python &> /dev/null; then
        PY_MAJOR=$(python --version 2>&1 | cut -d' ' -f2 | cut -d. -f1)
        if [ "$PY_MAJOR" = "3" ]; then
            PYTHON_CMD="python"
        fi
    fi
fi

if ! command -v $PYTHON_CMD &> /dev/null; then
    print_error "Python 3 is required but not found in PATH"
    exit 1
fi

print_success "$PYTHON_CMD is available"

# Check if pip is available
if ! $PYTHON_CMD -m pip --version &> /dev/null; then
    print_error "pip is required but not working with $PYTHON_CMD"
    exit 1
fi

print_success "pip is available"

# Check & extract studio parameters from the file
STUDIO_PARAMS_FILE="studio_parameters.json"                             # Hard coding the name for simplicity & to avoid command line parameters to the deploy.sh

print_status "Checking for studio parameters file..."
if [ ! -f "$STUDIO_PARAMS_FILE" ]; then
    print_error "Studio parameters file not found: $STUDIO_PARAMS_FILE"
    print_status "Creating template studio parameters file..."
    
    cat > "$STUDIO_PARAMS_FILE" << 'EOF'
{
  "StudioName": "Your Studio Name",
  "ContactEmail": "contact@yourstudio.com", 
  "GameTitle": "Your Game Title",
  "GameGenre": "action"
}
EOF

    print_status "Template created: $STUDIO_PARAMS_FILE"
    print_error ""
    print_error "🚨 REQUIRED: Please edit $STUDIO_PARAMS_FILE with your studio information:"
    print_error ""
    print_error "Required Parameters:"
    print_error "  StudioName   - Name of your game studio (e.g., 'Cosmic Games')"
    print_error "  ContactEmail - Your contact email (e.g., 'contact@cosmicgames.com')"
    print_error "  GameTitle    - Title of your game (e.g., 'Stellar Odyssey')"
    print_error "  GameGenre    - Genre of your game (e.g., 'space-rpg')"
    print_error ""
    print_error "Character Restrictions:"
    print_error "  ✅ Allowed: Letters, numbers, spaces, hyphens, underscores, periods"
    print_error "  ✅ Allowed: Parentheses (), exclamation marks !, ampersands &, at signs @"
    print_error "  ❌ Blocked: Quotes, brackets, slashes, greater/less than signs"
    print_error ""
    print_error "Example valid values:"
    print_error '  "StudioName": "Cosmic Games & Entertainment"'
    print_error '  "ContactEmail": "contact@cosmic-games.com"'
    print_error '  "GameTitle": "Stellar Odyssey: The Adventure!"'
    print_error '  "GameGenre": "space-rpg"'
    print_error ""
    print_error "After editing the file, run this script again to deploy."
    exit 1
fi

# Validate studio parameters file
print_status "Validating studio parameters..."
if $PYTHON_CMD -c "
import json
import sys
import re

try:
    with open('$STUDIO_PARAMS_FILE', 'r') as f:
        params = json.load(f)
    
    required_fields = ['StudioName', 'ContactEmail', 'GameTitle', 'GameGenre']
    missing_fields = [field for field in required_fields if not params.get(field) or params[field].strip() == '' or params[field] == f'Your {field.replace(\"Name\", \" Name\").replace(\"Email\", \" Email\").replace(\"Title\", \" Title\").replace(\"Genre\", \" Genre\")}']
    
    if missing_fields:
        print(f'❌ Missing or template values in required fields: {missing_fields}')
        sys.exit(1)
    
    # Validate character patterns
    allowed_pattern = r'^[a-zA-Z0-9\s\-_.()!&@]+$'
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    for field in ['StudioName', 'GameTitle', 'GameGenre']:
        if not re.match(allowed_pattern, params[field]):
            print(f'❌ Invalid characters in {field}: {params[field]}')
            print(f'   Only letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, and at signs are allowed')
            sys.exit(1)
    
    if not re.match(email_pattern, params['ContactEmail']):
        print(f'❌ Invalid email format: {params[\"ContactEmail\"]}')
        sys.exit(1)
    
    print('✅ Studio parameters validation passed')
    sys.exit(0)  # Explicitly exit with success code
    
except json.JSONDecodeError as e:
    print(f'❌ Invalid JSON in {\"$STUDIO_PARAMS_FILE\"}: {e}')
    sys.exit(1)
except Exception as e:
    print(f'❌ Error validating studio parameters: {e}')
    sys.exit(1)
"; then
    print_success "Studio parameters validated successfully"
else
    print_error "Studio parameters validation failed"
    print_error "Please fix the issues in $STUDIO_PARAMS_FILE and try again"
    exit 1
fi

# Extract parameters for CDK deployment
STUDIO_NAME=$($PYTHON_CMD -c "import json; print(json.load(open('$STUDIO_PARAMS_FILE'))['StudioName'])")
CONTACT_EMAIL=$($PYTHON_CMD -c "import json; print(json.load(open('$STUDIO_PARAMS_FILE'))['ContactEmail'])")
GAME_TITLE=$($PYTHON_CMD -c "import json; print(json.load(open('$STUDIO_PARAMS_FILE'))['GameTitle'])")
GAME_GENRE=$($PYTHON_CMD -c "import json; print(json.load(open('$STUDIO_PARAMS_FILE'))['GameGenre'])")

print_status "Studio Parameters:"
print_status "  Studio Name: $STUDIO_NAME"
print_status "  Contact Email: $CONTACT_EMAIL"
print_status "  Game Title: $GAME_TITLE"
print_status "  Game Genre: $GAME_GENRE"

# Check if CDK is available
if ! command -v cdk &> /dev/null; then
    print_error "AWS CDK is required but not installed"
    print_error "Install with: npm install -g aws-cdk"
    exit 1
fi

print_success "AWS CDK is available"

# Check if AWS CLI is configured
print_status "Checking AWS CLI configuration..."
if ! aws sts get-caller-identity &> /dev/null; then
    print_warning "AWS CLI is not configured or credentials are invalid"
    print_status "Please configure AWS CLI with your credentials:"
    echo ""
    echo "Option 1: Use AWS CLI configure"
    echo "  aws configure"
    echo ""
    echo "Option 2: Set environment variables"
    echo "  export AWS_ACCESS_KEY_ID=your_access_key"
    echo "  export AWS_SECRET_ACCESS_KEY=your_secret_key"
    echo "  export AWS_DEFAULT_REGION=us-east-1"
    echo ""
    echo "Option 3: Use IAM roles (if running on EC2)"
    echo "  Attach an appropriate IAM role to your EC2 instance"
    echo ""
    
    read -p "Press Enter after configuring AWS credentials, or Ctrl+C to abort..."
    
    # Check again
    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "AWS CLI is still not configured properly"
        exit 1
    fi
fi

# Show AWS identity
AWS_IDENTITY=$(aws sts get-caller-identity --output text --query 'Arn' 2>/dev/null || echo "Unknown")
print_success "AWS CLI configured. Identity: $AWS_IDENTITY"

print_success "Prerequisites check passed"

# Validate system before deployment
print_status "Running system validation..."
if [ -f "validate_system.py" ]; then
    if ! $PYTHON_CMD validate_system.py; then
        print_error "System validation failed. Please fix errors before deployment."
        exit 1
    fi
    print_success "System validation passed"
else
    print_warning "System validation script not found. Skipping validation."
fi

# Build Lambda Layers
print_status "Building Lambda Layers..."
if [ -f "layers/build_layer.sh" ]; then
    cd layers
    chmod +x build_layer.sh
    if ! ./build_layer.sh; then
        print_error "Failed to build Lambda Layers"
        exit 1
    fi
    cd "$SCRIPT_DIR"
    print_success "Lambda Layers built successfully"
else
    print_warning "Layer build script not found at layers/build_layer.sh"
    print_status "Continuing without building layers..."
fi

# Install CDK dependencies
print_status "Installing CDK dependencies..."
if [ -f "package.json" ]; then
    print_status "Installing Node.js dependencies..."
    npm install
elif [ -f "requirements.txt" ]; then
    print_status "Installing Python dependencies..."
    $PYTHON_CMD -m pip install -r requirements.txt --user
fi

# Create CDK logs directory
mkdir -p cdk_logs

# Set CDK environment variables
export CDK_NEW_BOOTSTRAP=1
export CDK_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-west-2}

# Get deployment environment (default to dev)
DEPLOY_ENVIRONMENT=${ENVIRONMENT:-dev}

# Validate environment
if [[ ! "$DEPLOY_ENVIRONMENT" =~ ^(dev|staging|prod)$ ]]; then
    print_error "Invalid environment: $DEPLOY_ENVIRONMENT"
    print_error "Valid environments are: dev, staging, prod"
    print_error "Usage: ENVIRONMENT=staging ./deploy.sh"
    exit 1
fi

print_status "Deployment environment: $DEPLOY_ENVIRONMENT"

# Bootstrap CDK if needed
print_status "Checking CDK bootstrap status..."
if ! cdk bootstrap --version-reporting=false > cdk_logs/bootstrap.log 2>&1; then
    print_error "CDK bootstrap failed. Check cdk_logs/bootstrap.log for details"
    cat cdk_logs/bootstrap.log
    exit 1
fi
print_success "CDK bootstrap completed (details in cdk_logs/bootstrap.log)"

# Synthesize CDK template
print_status "Synthesizing CDK template..."
if ! cdk synth GameStatsLeaderboardsStack > cdk_logs/synthesis.log 2>&1; then
    print_error "CDK synthesis failed. Check cdk_logs/synthesis.log for details"
    cat cdk_logs/synthesis.log
    exit 1
fi
print_success "CDK synthesis completed (details in cdk_logs/synthesis.log)"

# Deploy the stack
print_status "Deploying GameStatsLeaderboardsStack..."
echo ""
echo "This will deploy:"
echo "  • Your initial studio and first game registration"
echo "  • Studio API Key generation and secure storage"
echo "  • Lambda Layer with Valkey-GLIDE and dependencies"
echo "  • 10 Lambda functions with shared layer"
echo "  • MemoryDB cluster for high-performance caching"
echo "  • DynamoDB tables for configuration and statistics"
echo "  • API Gateway with authentication"
echo "  • VPC with optimized networking"
echo "  • CloudWatch monitoring and alarms"
echo ""

# Deploy main stack
if ! cdk deploy GameStatsLeaderboardsStack \
    --context environment=$DEPLOY_ENVIRONMENT \
    --context enable_resource_reuse=true \
    --context force_create_new=false \
    --parameters StudioName="$STUDIO_NAME" \
    --parameters ContactEmail="$CONTACT_EMAIL" \
    --parameters GameTitle="$GAME_TITLE" \
    --parameters GameGenre="$GAME_GENRE" \
    --require-approval never \
    --outputs-file cdk_logs/stack_outputs.json; then
    print_error "CDK deployment failed"
    exit 1
fi

print_success "Game Stats and Leaderboards' Main Stack Deployment completed successfully!"

print_status "Deploying GameStatsLeaderboardsMonitoringStack..."
# Deploy monitoring stack if available
if [ -f "app_post_deploy.py" ]; then
    print_status "Deploying monitoring stack..."
    if ! cdk deploy GameStatsLeaderboardsMonitoringStack \
        --context environment=$DEPLOY_ENVIRONMENT \
        --context base_stack_name=GameStatsLeaderboardsStack \
        --context enable_debug_mode=true \
        --app "$PYTHON_CMD app_post_deploy.py" \
        --require-approval never \
        --outputs-file cdk_logs/monitoring_outputs.json; then
        print_error "Advanced Monitoring CDK deployment failed"
        exit 1
    fi
    
    print_success "Game Stats and Leaderboards Ancillary Advanced Monitoring Stack Deployment completed successfully!"
else
    print_warning "Monitoring stack deployment script not found. Skipping monitoring deployment."
fi

# Get stack outputs
print_status "Retrieving deployment information..."
API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name GameStatsLeaderboardsStack \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

STUDIO_API_KEY=$(aws cloudformation describe-stacks \
    --stack-name GameStatsLeaderboardsStack \
    --query 'Stacks[0].Outputs[?OutputKey==`StudioAPIKey`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

STUDIO_ID=$(aws cloudformation describe-stacks \
    --stack-name GameStatsLeaderboardsStack \
    --query 'Stacks[0].Outputs[?OutputKey==`StudioId`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

GAME_ID=$(aws cloudformation describe-stacks \
    --stack-name GameStatsLeaderboardsStack \
    --query 'Stacks[0].Outputs[?OutputKey==`GameId`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

LAYER_ARN=$(aws cloudformation describe-stacks \
    --stack-name GameStatsLeaderboardsStack \
    --query 'Stacks[0].Outputs[?OutputKey==`SharedLayerArn`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

echo ""
echo "🎉 Deployment Summary (Note: You can also find these in the CloudFormation Stack Outputs, and your SSM Parameter Store!)"
echo "========================================================================================================================"
echo "Stack Name: GameStatsLeaderboardsStack"
echo "API Endpoint: $API_ENDPOINT"
echo "Studio API Key: $STUDIO_API_KEY"
echo "Studio ID: $STUDIO_ID"
echo "Game ID: $GAME_ID"
echo "Layer ARN: $LAYER_ARN"
echo "Region: ${AWS_DEFAULT_REGION:-us-west-2}"
echo "AWS Identity: $AWS_IDENTITY"
echo ""

# Test deployment
print_status "Testing deployment..."
if [ "$API_ENDPOINT" != "Not available" ] && [ "$API_ENDPOINT" != "" ]; then
    # Test health endpoint
    print_status "Validating the API health endpoint..."
    if curl -s -f "$API_ENDPOINT/health" > /dev/null 2>&1; then
        print_success "API endpoint is responding"
    else
        print_warning "API endpoint may not be ready yet (this is normal for new deployments)"
        print_status "Waiting 30 seconds and trying again..."
        sleep 30
        if curl -s -f "$API_ENDPOINT/health" > /dev/null 2>&1; then
            print_success "API endpoint is now responding"
        else
            print_warning "API endpoint still not responding - may need more time to initialize"
        fi
    fi
else
    print_warning "Could not retrieve API endpoint for a sanity check..."
fi

# Create convenience scripts
print_status "Creating convenience scripts..."

# Create deployment info script
cat > deployment_info.sh << EOF
#!/bin/bash
# Game Stats & Leaderboards Deployment Information

echo "🎮 $GAME_NAME's Stats & Leaderboards System"
echo "=================================="
echo ""
echo "API Endpoint: $API_ENDPOINT"
echo "Layer ARN: $LAYER_ARN"
echo "Region: ${AWS_DEFAULT_REGION:-us-east-1}"
echo "AWS Identity: $AWS_IDENTITY"
echo ""
echo "Studio API Key:"
aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==\`StudioAPIKey\`].OutputValue' --output text 2>/dev/null || echo "Not available"
echo ""
echo "Studio ID:"
aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==\`StudioId\`].OutputValue' --output text 2>/dev/null || echo "Not available"
echo ""
echo "Game ID:"
aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==\`GameId\`].OutputValue' --output text 2>/dev/null || echo "Not available"
echo ""
echo "Stack Status:"
aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "Stack not found"
echo ""
echo "Recent Events:"
aws cloudformation describe-stack-events --stack-name GameStatsLeaderboardsStack --max-items 5 --query 'StackEvents[*].[Timestamp,ResourceStatus,LogicalResourceId]' --output table 2>/dev/null || echo "No events found"
EOF

chmod +x deployment_info.sh

print_success "Convenience script created: deployment_info.sh"

echo ""
echo "🚀 Next Steps"
echo "============="

# Extract actual values for immediate use
print_status "Extracting deployment values..."
API_KEY=$(aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==`StudioAPIKey`].OutputValue' --output text 2>/dev/null)
STUDIO_ID=$(aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==`StudioId`].OutputValue' --output text 2>/dev/null)
GAME_ID=$(aws cloudformation describe-stacks --stack-name GameStatsLeaderboardsStack --query 'Stacks[0].Outputs[?OutputKey==`GameId`].OutputValue' --output text 2>/dev/null)

echo ""
echo "1. Your deployment values:"
echo "   API Endpoint: $API_ENDPOINT"
echo "   Studio API Key: ${API_KEY:0:20}..."
echo "   Studio ID: $STUDIO_ID"
echo "   Game ID: $GAME_ID"
echo ""
echo "2. Update game information (if needed):"
echo "   curl -X POST \"$API_ENDPOINT/developer/register\" \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -H \"Authorization: Bearer $API_KEY\" \\"
echo "     -d '{\"studioName\": \"Your Studio\", \"contactEmail\": \"you@studio.com\", \"gameTitle\": \"Updated Game Title\", \"gameGenre\": \"action\"}'"
echo ""
echo "3. Test your API endpoints:"
echo "   # Get developer info"
echo "   curl -X GET \"$API_ENDPOINT/developer/info?studioId=$STUDIO_ID&gameId=$GAME_ID\" \\"
echo "     -H \"Authorization: Bearer $API_KEY\""
echo ""
echo "   # Test leaderboard configuration"
echo "   curl -X POST \"$API_ENDPOINT/leaderboards/configs\" \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -H \"Authorization: Bearer $API_KEY\" \\"
echo "     -d '{\"gameLeaderboardConfig\":{\"gameID\":\"$GAME_ID\",\"gameMode\":\"campaign\"}}'"
echo ""
echo "4. Run comprehensive tests (if available):"
echo "   ./test_devreg.sh      # Test developer registration API"
echo "   ./test_leaderconfigs.sh  # Test leaderboard configuration API"
echo ""
echo "5. View the system documentation:"
echo "   cat readme.md"
echo ""
echo "6. Check deployment status anytime:"
echo "   ./deployment_info.sh"
echo ""
echo "📋 Important Notes:"
echo "==================="
echo "• This deployment infrastructure has been set up for $STUDIO_NAME's - $GAME_TITLE"
echo "• Ideally, deploy separate stacks for each of your games in their own isolated AWS environments"
echo "• /developer/register updates the current game's information, if needed post-deployment"
echo "• Each game gets dedicated, isolated infrastructure"
echo "• The API key is available in CloudFormation outputs and SSM Parameter Store"
echo "• All API endpoints require authentication via Authorization: Bearer header"
echo ""

print_success "🎉 Deployment script completed successfully!"
print_success "Your $GAME_TITLE's Stats & Leaderboards system is ready to use!"
