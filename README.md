This project contains a series of web automation scripts for the daily tasks of the Learner Experience Team at Red Hat. 
- Automated lab environment creation
- Semi-automated QA for RHxxx courses
- SNOW auto-assign tickets
- Intercom status change


## Setup

### Requisites
- Root access to your laptop
- ansible core
- python3
- selenium libraries
`pip3 install selenium`
- [geckodriver](https://github.com/mozilla/geckodriver/releases) and/or [Chromedriver](https://chromedriver.chromium.org/downloads) under `/usr/bin/`


Run the `setup.yml` playbook that will install the requirements and wrapper scripts to create the labs:
``` 
$ ansible-playbook playbooks/setup.yml -K
```

### SSO for Associates
As you may have seen, the `setup.yml` playbook generated a `secret` that we will use to create our SSO token. 
```

TASK [Use this secret string to create your token.redhat.com] *************************************************************************************************************************************************************************************************************************************************************
ok: [localhost] => {
    "generated_secret.stdout": "4439be1a......................bc2d3ec263"
}
...output omitted...
```

Fill in the `playbooks/vars/credentials.yml` file with your `rol.redhat.com` username. Vault-encrypting this file is recommended:
```
# SSO Red Hat credentials
username: "rh-username"	                            # without @redhat.com
secret: "4439be1a......................bc2d3ec263"          # OTP Key to generate the SSO token
pin: "yourpin" 		                                    # Create a PIN for the OTP Key

# Github credentials
github_username: ""
github_password: ""
```


Go to `token.redhat.com` with the VPN activated to create the new token with the given secret. Uncheck the ☑️ `Generate OTP Key on the Server` box, paste your secret, and choose a PIN.

![image](https://user-images.githubusercontent.com/80515069/177427661-7a1d9c81-ad96-485c-a31a-376e7dc3c1e5.png)

Make sure that the `./counter` file always matches the `Count` value of the token, **initially set to 1**. It will increase the value each time you login.

![hotp](https://user-images.githubusercontent.com/80515069/212667043-69dd2e9e-c81e-4b75-a5ac-41e1b52b8f27.png)

### Other learners

Fill in the credentials.yml file with your rol.redhat.com credentials:
```
username: "youruser@mail.com"
password: "yourpassword"
``` 


# Automated lab environment creation
This script uses ansible and selenium to create, delete, and extend life of most used labs in rol.redhat.com:
  - rh124-9.0
  - rh134-9.0
  - rh199-9.0
  - rh294-9.0
  - do180-4.10
  - do280-4.10
  - do288-4.10
  - do447-2.8

It uses ansible-playbook to generate python scripts from templates that will launch a browser by using selenium.

## Running the thing

The `setup.yml` playbook has placed in your `/usr/local/bin/` dir some wrapper scripts that use the `rol-prod` environment by default. So if you want to use stage, append `rol-stage` as the second parameter.
- start
- delete
- recreate
- impersonate (only rol-prod)

The wrapper script will look into the list of courses and match the latest version of the course if you write only the number of the course.

[rol-start-render.webm](https://user-images.githubusercontent.com/80515069/214608957-41e14cd4-1084-45fc-bd4a-3e08cc34cf84.webm)

```
$ which start
/usr/local/bin/start

$ start 180 rol-stage

Course starting: do180-4.10
Environment: rol-stage

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

You can also directly run the playbooks to start the labs:
``` 
$ ansible-playbook playbooks/create.yml

``` 

An example to override the variables without editing the yaml files by using the extra-vars parameter.
```
$ ansible-playbook delete.yml \
        -e 'lab_environment=rol-stage' \
        -e '{"course_id": ["rh124-8.2", "rh134-8.2"]}'
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
        - rh294-9.0
        ...
```
# Semi-automated QA for RHxxx courses
This script will assist you in the introduction of the commands during any QA of the RH series, which are mostly done using the command line interface. It has some limitations, such those exercise where it's asked the user to open a new terminal tab, or that commands may need to be introduced directly into a different host without sshing it first, or with commands that require some custom user input. 

Despite these limitations, the script has proven to serve of great help during E2E QAs, reducing the human work to merely check that the output of the commands correspond to the steps in the guide.

It's possible to start the QA from any chapter and the script will continue from that point until the end. 

`ansible-playbook playbooks/operate-lab.yml -e action='qa' -e lab_environment='rol' -e '{"course_id": ["do180-4.10"]}' -e chapter_and_section='ch03s07'`

This will only create the script, that must be run manually.

`python3 /tmp/qa-do180-4.10-rol.py`

![image](https://user-images.githubusercontent.com/80515069/223105629-ad222ab3-db3f-483e-a096-cca121fd5edd.png)

`[...]`

![image](https://user-images.githubusercontent.com/80515069/223100817-611e988b-35c6-477f-b19c-f82af01d0da3.png)

`[...]`

![image](https://user-images.githubusercontent.com/80515069/223100891-15e241a7-37a8-4966-bff9-b8320118cd23.png)

At this point of the script, the lab is up and running, but needs 2 manual steps before hitting Enter to start the introduction of the commands: 
 - opening a terminal
 - disabling key repetition on `Settings > Universal Access > Typing > Repeat Keys = Off`

Note that this script is run in headless mode to avoid any manual interruption of the user in the automation tool, so you will need to open a new session with (i.e.)  `start 180` to have your monitoring workstation terminal.

# SNOW auto-assign tickets
The `playbook/snow.yml` will auto-assign any tickets in your queue that are not yet assigned to anybody. The need for this script emerged from the time-consuming task of filling all the field of each ticket, which came without the name, email, and summary filled.
I could have make this script simpler, but I finally decided to automate the whole thing to auto-assign the tickets to my queue as they come and automatically reply to the users.

To achieve this, I created in my favourites a customized query for `RHT Learner Experience - T2` group. This will correspond to a unique url that needs to be substituted in the `playbooks/templates/snow-auto-assign.yml` selenium template.       

![image](https://user-images.githubusercontent.com/80515069/212669278-29f9a09e-9fe0-427e-9ed3-3f25d92bde45.png)

Substitute your filter URL in the `driver.get` function parameter.

![image](https://user-images.githubusercontent.com/80515069/212669964-817f766d-ba67-463b-bf62-7a7124a86158.png)

Customize the email that will be sent to the customer as an ACK in the `auto_assign_tickets()` function.

![image](https://user-images.githubusercontent.com/80515069/212670415-862f3829-9bcb-42f6-8da9-79044584708b.png)

I created a crontab to periodically run the script every hour during my shift.
```bash
#######################
# Auto-assign tickets #
#######################

59 8-13 * * 1-5   export DISPLAY=:1 && SHELL=/usr/bin/bash && source ${HOME}/.bashrc && /usr/bin/ansible-playbook /home/carias/Documents/rol-lab-persistence/playbooks/snow.yml
```

# Jira ticket from SNOW Feedback
This script create a new jira from a SNOW feedback ticket. Review that everything is fine according to priority, categorization and a proper summary and description.

`ansible-playbook playbooks/jira.yml -e snow_id='RHT1915340'`

# Intercom status change
This script will switch your status on intercom to Away/Active.

`ansible-playbook playbooks/intercom.yml -e status="Away"`

![image](https://user-images.githubusercontent.com/80515069/223106095-6628576d-ba36-4c86-b258-856eca079b73.png)




