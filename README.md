# Automated lab environment creation
This project uses ansible and selenium to create, delete, and extend life of most used labs in rol.redhat.com:
  - rh124-8.2
  - rh134-8.2
  - rh294-8.4
  - rh199-8.2 
  - do180-4.6
  - do280-4.6
  - do288-4.6

## Requisites
- ansible-navigator
`pip3 install 'ansible-navigator[ansible-core]' --user`

## Setup
Fill in the credentials.yml file with your rol.redhat.com credentials:
```
username: "youruser@redhat.com"
password: "yourpassword"
``` 

Customize the environment (rol or rol-stage) and courses in the create/delete.yml vars section.
```
- name: ROL labs launcher
  hosts: localhost
  vars_files: credentials.yml
  vars:
    - lab_environment: "rol"
    - course_id: 
        - "rh124-8.2"
        - "rh134-8.2"
        - "rh294-8.4"
...
```

Run the playbook with ansible-navigator.
``` 
$ ansible-navigator run -m stdout create.yml

``` 

Another option is to override the vars by using the extra-vars parameter.
```
$ ansible-navigator run -m stdout delete.yml \ 
     --extra-vars='lab_environment=rol-stage' \
     --extra-vars='{"course_id": ["rh124-8.2", "rh134-8.2"]}'
```

## Recommendations
- The *create.yml* playbook will also increase the *Auto-destroy* box of the lab to the maximum available (usually 14 days), so it's recomended to create a cronjob that runs at least every 2 weeks.
- Sometimes labs get stuck and don't stop, so it's important to keep an eye on the *Lab hours used* counter to detect any abusive usage.
