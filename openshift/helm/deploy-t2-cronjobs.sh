#!/bin/bash

# T2 CronJobs Helm Chart Deployment Script
# This script installs or upgrades the T2 Snow auto-assignment CronJobs

set -e

CHART_NAME="t2-cronjobs"
NAMESPACE="lx-snow"
CHART_PATH="./t2-cronjobs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if helm is installed
if ! command -v helm &> /dev/null; then
    print_error "Helm is not installed. Please install Helm 3.x first."
    exit 1
fi

# Check if we're connected to the right cluster
if ! oc get namespace $NAMESPACE &> /dev/null; then
    print_error "Cannot access namespace '$NAMESPACE'. Please check your OpenShift connection."
    exit 1
fi

print_status "Deploying T2 CronJobs to namespace: $NAMESPACE"

# Check if release already exists
if helm list -n $NAMESPACE | grep -q $CHART_NAME; then
    print_warning "Release '$CHART_NAME' already exists. This will upgrade the existing deployment."
    ACTION="upgrade"
else
    print_status "Installing new release '$CHART_NAME'"
    ACTION="install"
fi

# Validate the chart first
print_status "Validating Helm chart..."
if ! helm lint $CHART_PATH; then
    print_error "Helm chart validation failed. Please fix the chart before deploying."
    exit 1
fi

# Show what will be deployed
print_status "Preview of resources to be deployed:"
helm template $CHART_NAME $CHART_PATH | grep -E "^(kind:|  name:)" | head -20

echo ""
read -p "Do you want to proceed with the $ACTION? (y/N): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_warning "Deployment cancelled."
    exit 0
fi

# Deploy the chart
print_status "Running helm $ACTION..."
if [ "$ACTION" = "install" ]; then
    helm install $CHART_NAME $CHART_PATH -n $NAMESPACE
else
    helm upgrade $CHART_NAME $CHART_PATH -n $NAMESPACE
fi

# Check deployment status
print_status "Checking CronJob status..."
sleep 2
oc get cronjobs -n $NAMESPACE -l app.kubernetes.io/part-of=snow-autoassign-t2

print_status "Deployment completed successfully!"

# Show next steps
echo ""
print_status "Next steps:"
echo "1. Monitor CronJob executions:"
echo "   oc get jobs -n $NAMESPACE -l app.kubernetes.io/part-of=snow-autoassign-t2"
echo ""
echo "2. Check logs for a specific assignee:"
echo "   oc logs job/snow-autoassign-t2-carlos-<timestamp> -n $NAMESPACE"
echo ""
echo "3. To modify schedules, edit values.yaml and run:"
echo "   helm upgrade $CHART_NAME $CHART_PATH -n $NAMESPACE"
echo ""
echo "4. To remove old individual CronJobs (after testing):"
echo "   oc delete cronjob snow-t2-carlos snow-t2-wasim snow-t2-samik snow-t2-chetan snow-t2-shashi -n $NAMESPACE" 