# T2 Snow Auto-Assignment CronJobs Helm Chart

This Helm chart creates individual CronJobs for each T2 team member to automatically assign ServiceNow tickets based on their scheduled shifts.

## Overview

The chart generates CronJobs based on the T1 deployment configuration but converts them to scheduled batch jobs for T2 team members:

- **Carlos Arias**: 06:10 daily (America/New_York)
- **Wasim Raja**: 18:05 daily (Asia/Kolkata)  
- **Samik Sanyal**: 00:00 daily (Asia/Kolkata)
- **Chetan Tiwary**: 00:19 daily (Asia/Kolkata)
- **Shashi Singh**: 00:14 daily (Asia/Kolkata)

## Prerequisites

1. OpenShift cluster with `lx-snow` namespace
2. `snow-autoassign-env-file` ConfigMap with environment variables
3. `snow-autoassign-t1` container image available in the registry
4. Helm 3.x installed

## Installation

### 1. Install the chart

```bash
helm install t2-cronjobs ./openshift/helm/t2-cronjobs -n lx-snow
```

### 2. Upgrade existing installation

```bash
helm upgrade t2-cronjobs ./openshift/helm/t2-cronjobs -n lx-snow
```

### 3. Customize values

Create a custom values file:

```bash
cp ./openshift/helm/t2-cronjobs/values.yaml custom-values.yaml
# Edit custom-values.yaml with your changes
helm install t2-cronjobs ./openshift/helm/t2-cronjobs -n lx-snow -f custom-values.yaml
```

## Configuration

### Adding/Removing Team Members

Edit `values.yaml` and add/remove entries in the `t2_schedules` section:

```yaml
t2_schedules:
  - name: newmember
    assignee: "New Member Name"
    schedule: "0 12 * * *"  # Daily at noon
    timezone: "UTC"
    suspend: false
```

### Modifying Schedules

Update the `schedule` field using standard cron format:
- `"0 9 * * 1-5"` - 9 AM, Monday through Friday
- `"*/30 * * * *"` - Every 30 minutes
- `"0 0 * * 0"` - Midnight every Sunday

### Suspending CronJobs

Set `suspend: true` to temporarily disable a specific assignee's cronjob.

## Maintenance Benefits

### Single Source of Truth
- All T2 schedules in one `values.yaml` file
- No need to edit multiple YAML files

### Easy Updates
- Add new team member: Add entry to `values.yaml` and run `helm upgrade`
- Change schedule: Modify `schedule` field and run `helm upgrade`
- Remove member: Delete entry and run `helm upgrade`

### Version Control
- Track all changes through Git
- Easy rollbacks with `helm rollback`

### Environment Management
- Different values files for dev/staging/prod
- Override specific values for testing

## Commands

```bash
# View current installation
helm list -n lx-snow

# View generated manifests
helm template t2-cronjobs ./openshift/helm/t2-cronjobs

# Check cronjob status
kubectl get cronjobs -n lx-snow -l app.kubernetes.io/part-of=snow-autoassign-t2

# View job history
kubectl get jobs -n lx-snow -l app.kubernetes.io/part-of=snow-autoassign-t2

# Uninstall
helm uninstall t2-cronjobs -n lx-snow
```

## Migration from Individual CronJobs

1. Install this Helm chart
2. Verify the new CronJobs are working correctly
3. Delete the old individual CronJob files:
   ```bash
   oc delete cronjob snow-t2-carlos -n lx-snow
   oc delete cronjob snow-t2-wasim -n lx-snow
   oc delete cronjob snow-t2-samik -n lx-snow
   oc delete cronjob snow-t2-chetan -n lx-snow
   oc delete cronjob snow-t2-shashi -n lx-snow
   ```

## Troubleshooting

### Check CronJob logs
```bash
# Get recent job for specific assignee
kubectl get jobs -n lx-snow -l assignee=carlos --sort-by=.metadata.creationTimestamp

# View job logs
kubectl logs job/snow-autoassign-t2-carlos-<timestamp> -n lx-snow
```

### Verify schedule syntax
```bash
# Test cron schedule
helm template t2-cronjobs ./openshift/helm/t2-cronjobs | grep -A 5 "schedule:"
``` 