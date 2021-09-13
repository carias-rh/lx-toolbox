# Automated rol.redhat.com lab creation
This project uses ansible and selenium to create and extend life of most used labs in rol.redhat.com:
  - rh124-8.2
  - rh134-8.2
  - rh294-8.4
  - do180-4.6
  - do280-4.6
  - do288-4.6
  - rh403-6.6

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
