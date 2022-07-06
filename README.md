# Automated lab environment creation
This project uses ansible and selenium to create, delete, and extend life of most used labs in rol.redhat.com:
  - rh124-9.0
  - rh134-9.0
  - rh199-9.0
  - rh294-8.4
  - do180-4.10
  - do280-4.10
  - do288-4.6
  - do447-2.8

It uses ansible-playbook to generate python scripts from templates that will launch a browser by using selenium.

## Setup
### Associates

Fill in the credentials.yml file with your rol.redhat.com username:
```
username: "yourusername"	                   # without @redhat.com
pin: "yourpin" 		                           # corresponding to the created token at token.redhat.com
secret: "4439be1a......................bc2d3ec263" # secret from the generated token
```

Generate the secret with the following command, and create a token with it.
```
$ dd if=/dev/random bs=1M count=1 status=none | shasum | cut -b 1-40
4439be1a......................bc2d3ec263

```

![image](https://user-images.githubusercontent.com/80515069/177427661-7a1d9c81-ad96-485c-a31a-376e7dc3c1e5.png)

Make sure that the "counter" file always matches the Count of the token.
![image](https://user-images.githubusercontent.com/80515069/177428398-59747c8c-1f9e-4904-8c15-7dc66e8c8f06.png)

### Other learners

Fill in the credentials.yml file with your rol.redhat.com credentials:
```
username: "youruser@redhat.com"
password: "yourpassword"
``` 

## Custom courses and environment
Customize the `lab_environment` and `courses_id` variables in the vars section:
```
$ cat create.yaml
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

## Running with ansible-playbook

### Requisites
- Root access to your laptop
- ansible core
- python3
- selenium libraries
`pip3 install selenium`
- [geckodriver](https://github.com/mozilla/geckodriver/releases) under `/usr/bin/`

Run the playbook with ansible-navigator.
``` 
$ ansible-playbook create.yml

``` 

Remember that you can override the variables without editing the yaml files by using the extra-vars parameter.
```
$ ansible-playbook delete.yml \
        -e 'lab_environment=rol-stage' \
        -e '{"course_id": ["rh124-8.2", "rh134-8.2"]}'
```

Install the wrappers at your convenience, such in /usr/local/bin

# Recommendations
- The *create.yml* playbook will also increase the *Auto-destroy* box of the lab to the maximum available (usually 14 days). I recomend to create a cronjob that runs at least every 2 weeks.
- Sometimes labs get stuck and don't stop, so it's important to keep an eye on the *Lab hours used* counter to detect any abusive usage.
- Due to fast changes in lab environment during development phases of a course, I recommend a daily cronjob to create and delete the lab to start each day with a new fresh lab environment.
