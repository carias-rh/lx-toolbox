# Setting Up Credentials for LX Toolbox

## Overview

The LX Toolbox uses a flexible credential management system that supports multiple methods for storing sensitive information securely.

## Priority Order

When looking for a credential, the system checks in this order:
1. **Environment Variables** (highest priority)
2. **Config File** (`config.ini`)
3. **Default Values** (if provided in code)

## Methods to Define Credentials

### Method 1: Using a .env File (Recommended for Local Development)

Create a `.env` file in the project root directory (`lx-toolbox/.env`):

```bash
# Lab Credentials for ROL environment
RH_USERNAME=your_username

# GitHub credentials for factory
GITHUB_USERNAME=your_github_username

# China environment credentials
CHINA_USERNAME=china_username

# ServiceNow API credentials
SNOW_INSTANCE_URL=https://yourinstance.service-now.com
SNOW_API_USER=your_snow_user
SNOW_API_PASSWORD=your_snow_password

# Jira API credentials
JIRA_SERVER_URL=https://yourjira.example.com
JIRA_API_USER=your_jira_user
JIRA_API_TOKEN=your_jira_api_token
```

**Important:** 
- Add `.env` to your `.gitignore` file to prevent committing credentials
- The `.env` file is automatically loaded by the `python-dotenv` library

### Method 2: Using System Environment Variables

Export environment variables in your shell:

```bash
# In bash/zsh
export RH_USERNAME="your_username"
export GITHUB_USERNAME="your_github_username"

# Make them permanent by adding to ~/.bashrc or ~/.zshrc
echo 'export RH_USERNAME="your_username"' >> ~/.bashrc
```

### Method 3: Using config.ini (Not Recommended for Sensitive Data)

You can add a `[Credentials]` section to `config/config.ini`:

```ini
[Credentials]
# Only use this for non-sensitive defaults
# DO NOT store passwords here if the file is committed to git
default_assignment_group = T2-Support
```

## How Credentials Are Retrieved in Code

When the code calls:
```python
username = self.config.get("Credentials", "RH_USERNAME")
```

The ConfigManager:
1. First checks for environment variable `RH_USERNAME`
2. Then checks for `CREDENTIALS_RH_USERNAME` 
3. Then looks in config.ini under `[Credentials]` for `rh_username`
4. Returns `None` if not found

## Login Process

**Important:** Login is **manual** and requires user interaction. The automation tools only assist by:
- **Autofilling usernames** to reduce repetitive typing
- Opening the browser and navigating to login pages


## Verifying Your Configuration

Run the config command to check if credentials are set (without showing values):

```bash
./lx-toolbox/scripts/lx-tool config
```

This will show:
```
[Credentials Status]
RH_USERNAME: ✓ Set
GITHUB_USERNAME: ✓ Set
...
```

## Security Best Practices

1. **Never commit credentials** to version control
2. **Use .env files** for local development
3. **Use environment variables** for production/CI/CD
4. **Consider using a secret manager** (like HashiCorp Vault, AWS Secrets Manager) for production
5. **Rotate credentials regularly**
6. **Use API tokens** instead of passwords when possible
7. **Limit credential scope** to minimum required permissions

## Troubleshooting

If you encounter issues with credentials, check:

1. The `.env` file exists in the project root
2. The variable names match exactly (case-sensitive)
3. No typos in variable names
4. The `.env` file has proper formatting (KEY=value, no spaces around =)
5. You're running the script from the correct directory

**Note:** If `RH_USERNAME` is not configured, the automation will prompt you to complete the login manually in the browser. 

## Example: Quick Setup for ROL

```bash
# 1. Copy the example file
cp .env.example .env

# 2. Edit the .env file
nano .env  # or vim, code, etc.

# 3. Add your username (password entry is manual for security)
RH_USERNAME=myusername

# 4. Test the configuration
./lx-toolbox/scripts/lx-tool config

# 5. Try starting a lab (you'll complete authentication in the browser)
./lx-toolbox/scripts/lx-tool lab start rh124-9.3
``` 