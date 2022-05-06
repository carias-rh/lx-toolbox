# Automated lab environment creation
This project uses ansible and selenium to create, delete, and extend life of most used labs in rol.redhat.com:
  - rh124-8.2
  - rh134-8.2
  - rh294-8.4
  - rh199-8.2 
  - do180-4.6
  - do280-4.6
  - do288-4.6

It has been implemented to be deployed with a simple ansible-plabook, ansible-navigator.
You will now found the different setups, from the easiest one to the one that may need more root permissions on your laptop. 

## Using ansible

### Setup
Fill in the credentials.yml file with your rol.redhat.com credentials:
```
username: "youruser@redhat.com"
password: "yourpassword"
``` 

Customize the lab_environment and courses_id variables in the create/delete.yml vars section.
```
- name: ROL labs launcher
  hosts: localhost
  vars_files: credentials.yml
  vars:
    - lab_environment: rol 	# Valid options are: rol / rol-stage
    - course_id:  		# Get the course_id from the URL, such https://rol.redhat.com/rol/app/courses/rh124-8.2
        - rh124-8.2
        - rh134-8.2
        - rh294-8.4
        ...
```
#### Running with ansible-navigator

## Requisites
- podman (from Internal Software catalog, tab 6 - Development)
- ansible-navigator
```
pip3 install 'ansible-navigator[ansible-core]' --user
export PATH=$PATH:~/.local/bin/
```

Run the playbook with ansible-navigator.
``` 
$ ansible-navigator run -m stdout create.yml

``` 

Remember you can override the variables without editing the yaml files by using the extra-vars parameter.
```
$ ansible-navigator run -m stdout delete.yml \ 
	-e 'lab_environment=rol-stage' \
	-e '{"course_id": ["rh124-8.2", "rh134-8.2"]}'
```

#### Running with ansible-playbook

##### Requisites
- Root access to your laptop
- ansible core
- python3
- selenium libraries
`pip3 install selenium`

Run the playbook with ansible-navigator.
``` 
$ ansible-playbook create.yml

``` 

## Recommendations
- The *create.yml* playbook will also increase the *Auto-destroy* box of the lab to the maximum available (usually 14 days). I recomend to create a cronjob that runs at least every 2 weeks.
- Sometimes labs get stuck and don't stop, so it's important to keep an eye on the *Lab hours used* counter to detect any abusive usage.
- Due to fast changes in lab environment during development phases of a course, I recommend a daily cronjob to create and delete the lab to start each day with a new fresh lab environment.
