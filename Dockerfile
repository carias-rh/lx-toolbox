#FROM ansible-automation-platform/ee-minimal-rhel8:2.13.9-1
FROM quay.io/carias_rh/lx-toolbox-base:0.1

MAINTAINER "Carlos M. Arias <carias@redhat.com>"

#RUN microdnf install -y epel-release python3-pip chromium.x86_64 unzip git\
#  && microdnf clean all \
#  && rm -rf /var/cache/{dnf,yum} 

#RUN pip3 install selenium --use-feature=2020-resolver \ 
#  && pip3 install --upgrade requests

WORKDIR /tmp
RUN git clone https://github.com/carias-rh/lx-toolbox.git

WORKDIR /tmp/lx-toolbox
RUN git checkout feature/snow_containerization && ansible-playbook ./playbooks/setup.yml

