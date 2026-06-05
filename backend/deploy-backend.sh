#!/bin/bash
#
# Mela AI - Backend Deployment Script
#

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
ENVIRONMENT="${1:-dev}"
RESOURCE_GROUP="rg-armely-mela-${ENVIRONMENT}"
APP_NAME="app-armely-mela-${ENVIRONMENT}-api"

print_info "Deploying Mela AI Backend..."
print_info "Environment: $ENVIRONMENT"
print_info "Resource Group: $RESOURCE_GROUP"
print_info "App Service: $APP_NAME"

# Check if we're in the right directory
if [ ! -f "requirements.txt" ]; then
    if [ -d "backend" ]; then
        cd backend
    else
        print_error "Please run this script from the project root or backend directory"
        exit 1
    fi
fi

# Build
print_info "Installing dependencies..."
pip install -r requirements.txt --quiet

# Create deployment package
print_info "Creating deployment package..."
rm -f deploy.zip
zip -r deploy.zip . -x "*.pyc" -x "__pycache__/*" -x ".git/*" -x "*.env*" -x "tests/*"

# Deploy to Azure
print_info "Deploying to Azure App Service..."
az webapp deployment source config-zip \
    --resource-group "$RESOURCE_GROUP" \
    --name "$APP_NAME" \
    --src deploy.zip

# Cleanup
rm -f deploy.zip

# Restart app
print_info "Restarting application..."
az webapp restart --resource-group "$RESOURCE_GROUP" --name "$APP_NAME"

# Run migrations
print_info "Running database migrations..."
az webapp ssh --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" \
    --command "cd /home/site/wwwroot && python -m alembic upgrade head" || true

print_success "Backend deployment completed!"
print_info "API URL: https://${APP_NAME}.azurewebsites.net"
