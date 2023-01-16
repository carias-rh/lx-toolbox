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

Fill in the `playbooks/vars/credentials.yml` file with your rol.redhat.com username. I recommend to vault-encrypt this file:
```
username: "yourusername"	                   # without @redhat.com
pin: "yourpin" 		                           # corresponding to the created token at token.redhat.com
secret: "4439be1a......................bc2d3ec263" # secret from the generated token
```

You can generate the `secret` for HOTP with the following command, we will then create a token with this secret.
```
$ dd if=/dev/random bs=1M count=1 status=none | shasum | cut -b 1-40
4439be1a......................bc2d3ec263

```

Go to `token.redhat.com` with the VPN activated to create a new token with the generated secret.

![image](https://user-images.githubusercontent.com/80515069/177427661-7a1d9c81-ad96-485c-a31a-376e7dc3c1e5.png)

Make sure that the `./counter` file always matches the `Count` value of the token, **initially set to 1**.

![hotp](https://user-images.githubusercontent.com/80515069/212667043-69dd2e9e-c81e-4b75-a5ac-41e1b52b8f27.png)

### Other learners

Fill in the credentials.yml file with your rol.redhat.com credentials:
```
username: "youruser@redhat.com"
password: "yourpassword"
``` 

## Custom courses and environment
You can customize the default `lab_environment` and `courses_id` variables in the vars section, so that you can create, start, delete multiple labs with a single run:
```
$ cat playbooks/create.yaml
- name: ROL labs launcher
  hosts: localhost
  vars_files: credentials.yml
  vars:
    - lab_environment: rol 	# Valid options are: rol / rol-stage
    - course_id:  		# Get the course_id from the URL, such https://rol.redhat.com/rol/app/courses/rh124-8.2
        - rh124-9.0
        - rh134-9.0
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
- [geckodriver](https://github.com/mozilla/geckodriver/releases) and/or [Chromedriver](https://chromedriver.chromium.org/downloads) under `/usr/bin/`


Run the setup playbook that will install python requirements and wrapper scripts:
``` 
$ ansible-playbook playbooks/setup.yml

``` 

You can directly run the playbooks to start the labs:
``` 
$ ansible-playbook playbooks/create.yml

``` 

An example to override the variables without editing the yaml files by using the extra-vars parameter.
```
$ ansible-playbook delete.yml \
        -e 'lab_environment=rol-stage' \
        -e '{"course_id": ["rh124-8.2", "rh134-8.2"]}'
```

However, the easiest way to use the playbooks is with the wrapper scripts under `./scripts` directory, by default they will use the rol-prod environment. They should be already installed if you ran the `setup.yml` playbook.
```
$ which start
/usr/local/bin/start

$ start 180 

Course starting: do180-4.10
Environment: rol-production

Using /etc/ansible/ansible.cfg as config file
[WARNING]: provided hosts list is empty, only localhost is available. Note that the implicit localhost does not match 'all'

PLAY [ROL labs launcher] *****************************************************************************************************************************************************************************************************************************************************************
...
``` 
Another wrapper example that allows you to impersonate a user:
```
$ impersonate 280 carias

Course starting: do280-4.10
Environment: rol-production
Impersonate: carias

Using /etc/ansible/ansible.cfg as config file
[WARNING]: provided hosts list is empty, only localhost is available. Note that the implicit localhost does not match 'all'

PLAY [ROL labs launcher] *****************************************************************************************************************************************************************************************************************************************************************
...
```

## Extended functionality
The `playbook/snow.yml` will auto-assign any tickets in your queue that are not yet assigned to anybody. To achieve this, I created in my favourites a customized query for `RHT Learner Experience - T2` group. This will correspond to a unique url that needs to be substituted in the `playbooks/templates/snow-auto-assign.yml` selenium template.       

![image](https://user-images.githubusercontent.com/80515069/212669278-29f9a09e-9fe0-427e-9ed3-3f25d92bde45.png)

Substitute your filter URL in the `driver.get` function parameter.

![image](https://user-images.githubusercontent.com/80515069/212669964-817f766d-ba67-463b-bf62-7a7124a86158.png)

Customize the email that will be sent to the customer as an ACK in the `auto_assign_tickets()` function.

![image](https://user-images.githubusercontent.com/80515069/212670415-862f3829-9bcb-42f6-8da9-79044584708b.png)

Create a crontab so that the script is periodically launched.
```bash
#######################
# Auto-assign tickets #
#######################

59 8-13 * * 1-5   export DISPLAY=:1 && SHELL=/usr/bin/bash && source ${HOME}/.bashrc && /usr/bin/ansible-playbook /home/carias/Documents/rol-lab-persistence/playbooks/snow.yml
``