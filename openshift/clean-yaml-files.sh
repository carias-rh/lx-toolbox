#!/bin/bash

# Create a loop to process all yaml files
for file in *.yml; do
  echo "Processing $file..."
  yq 'del(.status, .metadata.creationTimestamp, .metadata.resourceVersion, .metadata.selfLink, .metadata.uid, .metadata.generation, .metadata.annotations."kubectl.kubernetes.io/last-applied-configuration")' "$file" > "${file}.clean"
  mv "${file}.clean" "$file"
done