This project contains a series of web automation scripts for the daily tasks of the Learner Experience Team at Red Hat. 
- Automated lab environment creation
- Semi-automated QA for RH courses
- SNOW auto-assigns tickets
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
# ServiceNow with LLM

This is the combination of the tooling regarding Feedback tickets and course QA's. Make sure that have Ollama installed with the setup playbook.
```
git checkout feature/snow-ai
ansible-playbook playbooks/setup.yml -K
```

Put your name in the teammate_name variable into the snow-ai.yml playbook.

![Image](https://github.com/user-attachments/assets/e277e95f-89a1-427e-a537-74cc3c513caa)

Then, run the wrapper script `snow-ai` and use the first seconds to log into ServiceNow, ROL, and Jira, as the tabs start to open.

Go, grab a coffee☕ and have everything ready to work when you come back.


# Automated lab environment creation
These scripts can be used to create, recreate, delete, impersonate, and extend the life of most used labs in rol.redhat.com:

## Running the thing

The `setup.yml` playbook installed in the `/usr/local/bin/` dir some wrapper scripts that use the `rol-prod` environment by default. So if you want to use factory or china, append `rol-stage` or `china` as the second parameter.
- start
- delete
- recreate
- impersonate (only rol-prod)

The wrapper script will look into the list of courses and match the latest version of the course if you write only the number of the course.

[rol-start-render.webm](https://user-images.githubusercontent.com/80515069/214608957-41e14cd4-1084-45fc-bd4a-3e08cc34cf84.webm)

```
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



# Semi-automated QA for RH courses
This script copies and pastes the commands during any QA of the RH series, which are mostly done using the command line interface. It has some limitations, such as those exercises when the user is asked to open a new terminal tab, edit a file, or introduce commands that require some custom user input. 

Despite these limitations, the script has proven to be of great help during E2E QAs, reducing the human work to merely check that the output of the commands corresponds to the steps in the guide.

You will need the SSH key access to [github](https://www.freecodecamp.org/news/git-ssh-how-to/) configured to checkout the version of the course from repo to get the commands.

It's possible to start the QA from any chapter and the script will continue from that point until the end.  

`$ qa -c 180 -s ch02s02 -e china`

![image](https://user-images.githubusercontent.com/80515069/233403992-3e15964b-32c9-4f6a-95a3-ec0efa5bac42.png)

`[...]`

The script will get the commands from the indicated section, or the first guided exercise if not indicated. 

![image](https://user-images.githubusercontent.com/80515069/233404289-9ca4540e-4b00-4081-a8be-08f2bf5d7cf2.png)


`[...]`

![image](https://user-images.githubusercontent.com/80515069/233405072-26d7810c-0148-4841-9fb9-bbcaed416895.png)


At this point of the script, the lab is up and running, but needs 2 manual steps before hitting Enter to start the introduction of the commands: 
 - opening a terminal
 - disabling key repetition on `Settings > Universal Access > Typing > Repeat Keys = Off`

Note that this script is run in headless mode to avoid any manual interruption of the user in the automation tool, so you will need to open a new session with (i.e.)  `start 180` to have your monitoring workstation terminal.

You will notice that after introducing some kind of commands, a prompt to continue with the script execution will appear. This is to avoid the script running without control and introducing commands incorrectly. Have a look at some special commands that require these stopper prompts in the `operate-lab.py.j2` template. 

![image](https://user-images.githubusercontent.com/80515069/233406747-cc578ae1-d1c1-4b25-be4c-4b7084896c22.png)

How to steer the script in other exceptional situations:

- If for any reason, the script introduced an incorrect command, because it needed a customized input, or is missing some configuration file, you can stop the terminal process with `CTRL + s`. Then, resolve any inconsistencies in the exercise and resume the script execution with `CTRL + q`. 
- This is a work-in-progress project, mostly tested on RH courses, so if you find any other exceptional kind of command that needs a stopper prompt, please tell or submit a PR. Thanks!

# Jira ticket from SNOW Feedback
This script creates a new Jira from a SNOW feedback ticket. Review that everything is fine according to priority, categorization and a proper summary and description.

`$ jira RHT1915340`

# Intercom status change
This script will switch your status on Intercom to Away/Active.

`$ ansible-playbook playbooks/intercom.yml -e status="Away"`

![image](https://user-images.githubusercontent.com/80515069/223106095-6628576d-ba36-4c86-b258-856eca079b73.png)




