#!/bin/bash

################################################################################
# Honey Scraper Installation Script
# Comprehensive setup for Ubuntu/Debian systems
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR"
SERVICE_NAME="honey-scraper"
SERVICE_USER="${SUDO_USER:-$USER}"
PYTHON_CMD="python3"
PIP_CMD="pip3"

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

check_root() {
    if [[ $EUID -eq 0 ]] && [[ -z "$SUDO_USER" ]]; then
        print_error "This script should not be run directly as root."
        print_info "Please run as a regular user with sudo privileges:"
        echo "  sudo bash install.sh"
        exit 1
    fi
}

check_sudo() {
    if ! sudo -n true 2>/dev/null; then
        print_error "This script requires sudo privileges."
        echo "Please run with sudo: sudo bash install.sh"
        exit 1
    fi
}

check_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
        print_info "Detected OS: $PRETTY_NAME"
        
        if [[ "$OS" != "ubuntu" ]] && [[ "$OS" != "debian" ]]; then
            print_warning "This script is designed for Ubuntu/Debian."
            read -p "Continue anyway? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        print_warning "Cannot detect OS version."
    fi
}

check_installation_status() {
    local is_installed=0
    
    # Check if Python dependencies are installed
    if $PYTHON_CMD -c "import requests" 2>/dev/null; then
        is_installed=1
    fi
    
    # Check if service exists
    if systemctl list-unit-files | grep -q "^$SERVICE_NAME.service"; then
        is_installed=1
    fi
    
    # Check if database exists
    if [[ -f "$INSTALL_DIR/honey_stores.db" ]]; then
        is_installed=1
    fi
    
    echo $is_installed
}

install_system_packages() {
    print_header "Installing System Packages"
    
    print_info "Updating package lists..."
    sudo apt update -qq
    
    local packages=("python3" "python3-pip" "python3-venv" "sqlite3" "git" "curl" "wget")
    local to_install=()
    
    for pkg in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii  $pkg "; then
            to_install+=("$pkg")
        else
            print_success "$pkg is already installed"
        fi
    done
    
    if [[ ${#to_install[@]} -gt 0 ]]; then
        print_info "Installing: ${to_install[*]}"
        sudo apt install -y "${to_install[@]}"
        print_success "System packages installed"
    else
        print_success "All system packages already installed"
    fi
    
    # Verify Python version
    local py_version=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
    print_info "Python version: $py_version"
    
    # Check if Python is at least 3.6
    if $PYTHON_CMD -c "import sys; exit(0 if sys.version_info >= (3, 6) else 1)"; then
        print_success "Python version is compatible"
    else
        print_error "Python 3.6 or higher is required"
        exit 1
    fi
}

setup_virtual_environment() {
    print_header "Setting Up Python Virtual Environment"
    
    local venv_dir="$INSTALL_DIR/venv"
    
    if [[ -d "$venv_dir" ]]; then
        print_info "Virtual environment already exists"
        read -p "Recreate it? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "Removing old virtual environment..."
            rm -rf "$venv_dir"
        else
            print_info "Using existing virtual environment"
            return 0
        fi
    fi
    
    print_info "Creating virtual environment..."
    $PYTHON_CMD -m venv "$venv_dir"
    print_success "Virtual environment created"
    
    print_info "Upgrading pip..."
    "$venv_dir/bin/pip" install --upgrade pip setuptools wheel -q
    print_success "pip upgraded"
}

install_python_dependencies() {
    print_header "Installing Python Dependencies"
    
    local venv_dir="$INSTALL_DIR/venv"
    local pip_cmd="$venv_dir/bin/pip"
    
    if [[ ! -f "$INSTALL_DIR/requirements.txt" ]]; then
        print_error "requirements.txt not found in $INSTALL_DIR"
        exit 1
    fi
    
    print_info "Installing packages from requirements.txt..."
    "$pip_cmd" install -r "$INSTALL_DIR/requirements.txt" -q
    print_success "Python dependencies installed"
    
    # Verify installation
    if "$venv_dir/bin/python" -c "import requests" 2>/dev/null; then
        print_success "Dependencies verified successfully"
    else
        print_error "Failed to verify dependencies"
        exit 1
    fi
}

create_systemd_service() {
    print_header "Creating Systemd Service (template)"

    local template_file="/etc/systemd/system/${SERVICE_NAME}@.service"
    local venv_python="$INSTALL_DIR/venv/bin/python3"

    print_info "Writing template unit: $template_file"

    sudo tee "$template_file" > /dev/null <<EOF
[Unit]
Description=Honey Store Scraper Service (worker %i)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$venv_python $INSTALL_DIR/scraper.py --mode auto --workers 10 --worker-id %i --db $INSTALL_DIR/honey_stores_%i.db --delay 0.5
Restart=on-failure
RestartSec=10
StandardOutput=append:$INSTALL_DIR/scraper_%i.log
StandardError=append:$INSTALL_DIR/scraper_%i_err.log

[Install]
WantedBy=multi-user.target
EOF

    print_success "Template unit created: ${SERVICE_NAME}@.service"

    # Optional: remove any old non-template unit to avoid confusion.
    # (Fresh install should be clean anyway, but this prevents leftovers.)
    if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
        print_warning "Removing legacy non-template unit: /etc/systemd/system/${SERVICE_NAME}.service"
        sudo rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    fi

    print_info "Reloading systemd daemon..."
    sudo systemctl daemon-reload
    print_success "Systemd daemon reloaded"
}

create_web_service() {
    print_header "Creating Web Dashboard Service"
    
    local service_name="honey-scraper-web"
    local service_file="/etc/systemd/system/$service_name.service"
    local venv_python="$INSTALL_DIR/venv/bin/python"
    
    if [[ -f "$service_file" ]]; then
        print_warning "Web service file already exists"
        read -p "Overwrite it? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Skipping web service creation"
            return 0
        fi
    fi
    
    print_info "Creating web service file at $service_file..."
    
    sudo tee "$service_file" > /dev/null <<EOF
[Unit]
Description=Honey Scraper Web Dashboard
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$venv_python $INSTALL_DIR/web_dashboard.py --host 0.0.0.0 --port 5000
Restart=always
RestartSec=5
StandardOutput=append:$INSTALL_DIR/web_dashboard.log
StandardError=append:$INSTALL_DIR/web_dashboard_error.log

[Install]
WantedBy=multi-user.target
EOF

    print_success "Web service file created"
    
    sudo systemctl daemon-reload
    print_success "Systemd daemon reloaded"
}

create_run_script() {
    print_header "Creating Helper Scripts"
    
    # Create run script
    local run_script="$INSTALL_DIR/run.sh"
    
    cat > "$run_script" <<'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate
python scraper.py
EOF
    
    chmod +x "$run_script"
    print_success "Created run.sh"
    
    # Create status script
    local status_script="$INSTALL_DIR/status.sh"
    
    cat > "$status_script" <<EOF
#!/bin/bash
echo "=== Service Status ==="
sudo systemctl status $SERVICE_NAME --no-pager
echo ""
echo "=== Recent Logs ==="
tail -n 20 $INSTALL_DIR/scraper.log
echo ""
echo "=== Database Stats ==="
if [[ -f "$INSTALL_DIR/honey_stores.db" ]]; then
    sqlite3 $INSTALL_DIR/honey_stores.db "SELECT 
        (SELECT COUNT(*) FROM stores) as total_stores,
        (SELECT COUNT(*) FROM scraped_domains) as domains_scraped,
        (SELECT COUNT(*) FROM coupons) as total_coupons;"
else
    echo "Database not found"
fi
EOF
    
    chmod +x "$status_script"
    print_success "Created status.sh"
    
    # Create update script
    local update_script="$INSTALL_DIR/update.sh"
    
    cat > "$update_script" <<'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  Honey Scraper Update Script"
echo "========================================"
echo ""

# Pull latest changes FIRST (including update.sh and install.sh)
if [[ -d .git ]]; then
    echo "Pulling latest changes from git..."
    
    # Stash any local changes to avoid conflicts
    if ! git diff-index --quiet HEAD --; then
        echo "⚠ Local changes detected, stashing..."
        git stash
        STASHED=1
    fi
    
    # Pull updates
    git pull
    echo "✓ Git pull complete"
    echo "✓ Scripts updated (install.sh, update.sh, etc.)"
    
    # Check if update.sh itself was updated
    if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q "update.sh"; then
        echo ""
        echo "⚠ update.sh was updated! Re-running with new version..."
        sleep 2
        exec "$0" "$@"
    fi
fi

# Stop services
echo "Stopping services..."
sudo systemctl stop honey-scraper 2>/dev/null || true
sudo systemctl stop honey-scraper-web 2>/dev/null || true
echo "✓ Services stopped"

# Backup database
echo "Backing up database..."
if [[ -f honey_stores.db ]]; then
    cp honey_stores.db "honey_stores.db.backup.$(date +%Y%m%d_%H%M%S)"
    echo "✓ Database backed up"
fi

# Update database schema
echo "Updating database schema..."
source venv/bin/activate
python3 -c "
from scraper import HoneyScraper
scraper = HoneyScraper(db_path='honey_stores.db')
print('Database schema updated')
" 2>/dev/null || echo "⚠ Schema update skipped"

# Update Python dependencies
echo "Updating Python dependencies..."
pip install --upgrade -r requirements.txt -q
echo "✓ Dependencies updated"

# Restart services
echo ""
read -p "Restart services? (y/N): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl start honey-scraper
    sudo systemctl start honey-scraper-web
    echo "✓ Services restarted"
    echo ""
    echo "Check status with:"
    echo "  sudo systemctl status honey-scraper"
    echo "  sudo systemctl status honey-scraper-web"
fi

echo ""
echo "✓ Update complete!"
EOF
    
    chmod +x "$update_script"
    print_success "Created update.sh"
    
    # Create web start script
    local web_script="$INSTALL_DIR/start_web.sh"
    
    cat > "$web_script" <<'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate
python web_dashboard.py --host 0.0.0.0 --port 5000
EOF
    
    chmod +x "$web_script"
    print_success "Created start_web.sh"
}

configure_firewall() {
    print_header "Firewall Configuration"
    
    if command -v ufw &> /dev/null; then
        print_info "UFW firewall detected"
        
        read -p "Open port 5000 for web dashboard? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo ufw allow 5000/tcp
            print_success "Port 5000 opened for web dashboard"
        fi
    else
        print_info "UFW not installed, skipping firewall configuration"
    fi
}

set_permissions() {
    print_header "Setting Permissions"
    
    print_info "Setting ownership to $SERVICE_USER..."
    sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    
    print_info "Setting file permissions..."
    find "$INSTALL_DIR" -type f -name "*.py" -exec chmod 644 {} \;
    find "$INSTALL_DIR" -type f -name "*.sh" -exec chmod 755 {} \;
    
    print_success "Permissions set"
}

test_installation() {
    print_header "Testing Installation"
    
    local venv_python="$INSTALL_DIR/venv/bin/python"
    
    print_info "Testing Python script..."
    if "$venv_python" -c "from scraper import HoneyScraper; print('OK')" 2>/dev/null | grep -q "OK"; then
        print_success "Python script imports successfully"
    else
        print_error "Failed to import scraper module"
        return 1
    fi
    
    print_info "Testing database connection..."
    if "$venv_python" -c "import sqlite3; conn = sqlite3.connect(':memory:'); print('OK')" 2>/dev/null | grep -q "OK"; then
        print_success "SQLite working correctly"
    else
        print_error "SQLite connection failed"
        return 1
    fi
    
    print_success "All tests passed!"
}

show_usage_info() {
    print_header "Installation Complete!"
    
    # Get server IP
    local server_ip=$(hostname -I | awk '{print $1}')
    
    cat <<EOF
${GREEN}Installation successful!${NC}

${BLUE}Quick Start Commands:${NC}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

${YELLOW}Web Dashboard:${NC}
  sudo systemctl start honey-scraper-web    # Start web dashboard
  sudo systemctl enable honey-scraper-web   # Auto-start on boot
  
  ${GREEN}Access at: http://${server_ip}:5000${NC}
  ${GREEN}Or from any device: http://YOUR_SERVER_IP:5000${NC}

${YELLOW}Interactive Mode:${NC}
  ./run.sh                          # Run scraper interactively
  
${YELLOW}Scraper Service Management:${NC}
  sudo systemctl start $SERVICE_NAME     # Start scraping service
  sudo systemctl stop $SERVICE_NAME      # Stop scraping service
  sudo systemctl status $SERVICE_NAME    # Check service status
  sudo systemctl enable $SERVICE_NAME    # Auto-start on boot
  sudo systemctl disable $SERVICE_NAME   # Disable auto-start

${YELLOW}Web Dashboard Service:${NC}
  sudo systemctl start honey-scraper-web    # Start dashboard
  sudo systemctl stop honey-scraper-web     # Stop dashboard
  sudo systemctl status honey-scraper-web   # Check status

${YELLOW}Monitoring:${NC}
  ./status.sh                       # Show status and stats
  tail -f scraper.log               # Watch scraper logs
  tail -f web_dashboard.log         # Watch web dashboard logs
  
${YELLOW}Database:${NC}
  sqlite3 honey_stores.db           # Open database
  ./run.sh                          # Choose option 4 for stats

${YELLOW}Maintenance:${NC}
  ./update.sh                       # Update dependencies
  sudo bash install.sh              # Re-run installer

${BLUE}Recommended First Steps:${NC}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Start the web dashboard:
   ${GREEN}sudo systemctl start honey-scraper-web${NC}
   ${GREEN}sudo systemctl enable honey-scraper-web${NC}
   
2. Open in browser:
   ${GREEN}http://${server_ip}:5000${NC}
   
3. Use the web interface to:
   - Start scraping (test with 10 domains first)
   - Monitor progress in real-time
   - Browse and search the database
   - Export data to CSV/JSON

${BLUE}Files & Locations:${NC}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Install Dir:        $INSTALL_DIR
  Database:           $INSTALL_DIR/honey_stores.db
  Scraper Logs:       $INSTALL_DIR/scraper.log
  Web Dashboard Logs: $INSTALL_DIR/web_dashboard.log
  Services:           /etc/systemd/system/$SERVICE_NAME.service
                      /etc/systemd/system/honey-scraper-web.service
  Virtual Env:        $INSTALL_DIR/venv

${YELLOW}Need help?${NC} Check USAGE.md, DEPLOYMENT.md, or README.md

EOF
}

update_existing_installation() {
    print_header "Updating Existing Installation"
    
    print_info "Stopping services if running..."
    sudo systemctl stop $SERVICE_NAME 2>/dev/null || true
    sudo systemctl stop "${SERVICE_NAME}-web" 2>/dev/null || true
    print_success "Services stopped"
    
    print_info "Backing up database..."
    if [[ -f "$INSTALL_DIR/honey_stores.db" ]]; then
        cp "$INSTALL_DIR/honey_stores.db" "$INSTALL_DIR/honey_stores.db.backup.$(date +%Y%m%d_%H%M%S)"
        print_success "Database backed up"
    fi
    
    print_info "Updating database schema..."
    # Run Python to update database schema (creates missing tables/columns)
    "$INSTALL_DIR/venv/bin/python3" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
from scraper import HoneyScraper
scraper = HoneyScraper(db_path='$INSTALL_DIR/honey_stores.db')
print('Database schema updated')
" 2>/dev/null || print_warning "Database schema update skipped (scraper.py may have issues)"
    print_success "Database schema updated"
    
    print_info "Updating Python dependencies..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade -r "$INSTALL_DIR/requirements.txt" -q
    print_success "Dependencies updated"
    
    print_info "Recreating systemd services..."
    create_systemd_service
    create_web_service
    print_success "Services recreated"
    
    print_info "Recreating helper scripts..."
    create_run_script
    
    set_permissions
    
    print_success "Update complete!"
    
    read -p "Start the services now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo systemctl start $SERVICE_NAME
        sudo systemctl start "${SERVICE_NAME}-web"
        print_success "Services started"
        print_info "Scraper service: sudo systemctl status $SERVICE_NAME"
        print_info "Web dashboard: sudo systemctl status ${SERVICE_NAME}-web"
        print_info "Web URL: http://$(hostname -I | awk '{print $1}'):5000"
    fi
}

################################################################################
# Main Installation Flow
################################################################################

main() {
    clear
    print_header "HONEY SCRAPER INSTALLER"
    
    echo -e "${BLUE}This script will install and configure the Honey Store Scraper.${NC}\n"
    
    # Pre-flight checks
    check_root
    check_sudo
    check_os
    
    # Check if already installed
    local is_installed=$(check_installation_status)
    
    if [[ $is_installed -eq 1 ]]; then
        print_warning "Honey Scraper appears to be already installed."
        echo ""
        echo "Choose an option:"
        echo "  1) Update existing installation"
        echo "  2) Fresh installation (will recreate everything)"
        echo "  3) Exit"
        echo ""
        read -p "Enter choice (1-3): " -n 1 -r choice
        echo ""
        
        case $choice in
            1)
                update_existing_installation
                show_usage_info
                exit 0
                ;;
            2)
                print_info "Proceeding with fresh installation..."
                ;;
            3)
                print_info "Exiting..."
                exit 0
                ;;
            *)
                print_error "Invalid choice"
                exit 1
                ;;
        esac
    fi
    
    # Confirm installation
    echo ""
    read -p "Continue with installation? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Installation cancelled"
        exit 0
    fi
    
    # Installation steps
    install_system_packages
    setup_virtual_environment
    install_python_dependencies
    create_systemd_service
    create_web_service
    create_run_script
    configure_firewall
    set_permissions
    
    # Test installation
    if ! test_installation; then
        print_error "Installation completed with errors. Please review the output above."
        exit 1
    fi
    
    # Show usage information
    show_usage_info
    
    # Offer to start service
    echo ""
    read -p "Would you like to start the web dashboard now? (Y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        sudo systemctl enable honey-scraper-web
        sudo systemctl start honey-scraper-web
        print_success "Web dashboard started and enabled"
        
        local server_ip=$(hostname -I | awk '{print $1}')
        echo ""
        print_info "Web dashboard is now available at:"
        echo -e "  ${GREEN}http://${server_ip}:5000${NC}"
        echo -e "  ${GREEN}http://localhost:5000${NC} (from this machine)"
        echo ""
    else
        print_info "You can start it later with: sudo systemctl start honey-scraper-web"
    fi
    
    echo ""
    read -p "Would you like to start the scraper service now? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        WORKER_ID="${WORKER_ID:-0}"

        sudo systemctl daemon-reload

        # Avoid accidentally running worker 0 if this droplet is not worker 0
        if [[ "$WORKER_ID" != "0" ]]; then
            sudo systemctl disable --now "${SERVICE_NAME}@0.service" 2>/dev/null || true
        fi

        sudo systemctl enable --now "${SERVICE_NAME}@${WORKER_ID}.service"

        print_success "Scraper service started and enabled: ${SERVICE_NAME}@${WORKER_ID}.service"
        print_info "Check status with: sudo systemctl status ${SERVICE_NAME}@${WORKER_ID} --no-pager"
        print_info "View logs with: journalctl -u ${SERVICE_NAME}@${WORKER_ID} -f"
    else
        print_info "You can start it later with:"
        print_info "  sudo systemctl enable --now ${SERVICE_NAME}@${WORKER_ID:-0}.service"
    fi

    
    echo ""
    print_success "Installation complete!"
}

# Run main function
main "$@"
