FROM ansible-automation-platform-20-early-access/ee-supported-rhel8:2.0.1-6.1634243686

MAINTAINER "Carlos M. Arias <carias@redhat.com>"

RUN microdnf install -y chromium chromedriver \
  && microdnf clean all \
  && rm -rf /var/cache/{dnf,yum} 

RUN pip3 install selenium --use-feature=2020-resolver 
