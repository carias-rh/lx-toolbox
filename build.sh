#!/bin/bash
podman build --build-arg GIT_ACCESS_TOKEN="$(grep ghp_G /home/carias/.git-token)" --no-cache -t quay.io/carias_rh/lx-toolbox:1.0 . \
    &&  podman push quay.io/carias_rh/lx-toolbox:1.0
 
podman tag quay.io/carias_rh/lx-toolbox:1.0 quay.io/carias_rh/lx-toolbox:latest
podman push quay.io/carias_rh/lx-toolbox:latest
