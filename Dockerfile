#FROM ansible-automation-platform/ee-minimal-rhel8:2.13.9-1
#FROM docker.io/geerlingguy/docker-fedora39-ansible
FROM quay.io/carias_rh/lx-toolbox-base:0.2

MAINTAINER "Carlos M. Arias <carias@redhat.com>"

USER root

#COPY google-chrome-stable_current_x86_64.rpm /var/tmp/
#RUN dnf install -y /var/tmp/google-chrome-stable_current_x86_64.rpm
#
#RUN dnf install -y chromium-123.0.6312.86-1.fc39.x86_64 unzip git \
#  && dnf clean all \
#  && rm -rf /var/cache/{dnf,yum}
#
#RUN pip3 install selenium \
#  && pip3 install --upgrade requests
#
#RUN ln -s /usr/bin/python3 /usr/bin/python && \
#    ln -s /usr/local/bin/ansible-playbook /usr/bin/ansible-playbook

RUN mkdir /app

ENV HOME /app

WORKDIR /app
RUN git clone https://github.com/carias-rh/lx-toolbox.git

WORKDIR /app/lx-toolbox
RUN cd /app/lx-toolbox; ls -al

RUN git checkout feature/snow_containerization && ansible-playbook ./playbooks/setup.yml -t scripts

RUN chmod -R g+rwX /app
