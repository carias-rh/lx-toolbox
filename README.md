# rol-lab-persistence
Automation with ansible and selenium to start and extend life of most used labs in rol.redhat.com

## Requisites
- selenium-side-runner

## Setup
Create a credentials.yml file with the following variables and fill in with your rol.redhat.com credentials

```
ansible-vault create credentials.yml

---
username: "youruser@redhat.com"
password: "yourpassword"
``` 

Create a file with your ansible vault password

``` 
echo "yourpassword" > ../vault
chmod 600 ../vault
``` 

Run the playbook

``` 
ansible-playbook main.yml --vault-password-file ../vault

``` 
