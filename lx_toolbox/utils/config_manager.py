import os
import configparser
from dotenv import load_dotenv

class ConfigManager:
    def __init__(self, config_file_path="config/config.ini", env_file_path=".env"):
        # Load environment variables from .env file first
        # This allows .env to override system environment variables if needed for local dev
        # It will also gracefully fail if .env doesn't exist.
        load_dotenv(dotenv_path=env_file_path)

        # Disable interpolation to allow % characters in URLs (e.g., URL-encoded query strings)
        self.config = configparser.ConfigParser(interpolation=None)
        
        # Check if the config file exists before trying to read it
        if not os.path.exists(config_file_path):
            print(f"Warning: Config file not found at {config_file_path}. Using defaults and environment variables only.")
            self._config_file_data = {}
        else:
            self.config.read(config_file_path)
            self._config_file_data = {s: dict(self.config.items(s)) for s in self.config.sections()}

        # Prioritize:
        # 1. Environment variables
        # 2. Values from config file
        # 3. Default values (if provided in getter methods)

    def get(self, section: str, key: str, default=None):
        # Try to get from environment variables (uppercase)
        env_var_name = f"{section.upper()}_{key.upper()}"
        value = os.getenv(env_var_name)
        if value is not None:
            return self._cast_value(value)

        # Try to get from specific environment variable (as is, for direct .env mapping)
        # e.g. RH_USERNAME directly, not GENERAL_RH_USERNAME
        value_direct_env = os.getenv(key.upper())
        if value_direct_env is not None:
             return self._cast_value(value_direct_env)

        # Try to get from config file
        # ConfigParser stores keys as lowercase, so we need to check with lowercase key
        if section in self._config_file_data:
            # Try with the key as-is first
            if key in self._config_file_data[section]:
                return self._cast_value(self._config_file_data[section][key])
            # Try with lowercase key (how ConfigParser stores them)
            lowercase_key = key.lower()
            if lowercase_key in self._config_file_data[section]:
                return self._cast_value(self._config_file_data[section][lowercase_key])
        
        return default

    def _cast_value(self, value: str):
        """
        Tries to cast string value to boolean, integer, or float.
        Otherwise, returns the string.
        """
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def get_lab_base_url(self, environment: str) -> str | None:
        """Returns the base URL for a given lab environment."""
        # Lab environment keys in config.ini are like 'rol_base_url'
        config_key = f"{environment.lower().replace('-', '_')}_base_url"
        return self.get("LabEnvironments", config_key)

## Example usage (typically, you'd instantiate this once and pass it around or use a singleton)
#if __name__ == '__main__':
#    # Create dummy files for testing
##    if not os.path.exists("config"):
##        os.makedirs("config")
##    with open("config/config.ini", "w") as f:
##        f.write("[General]\n")
##        f.write("default_selenium_driver = chrome\n")
##        f.write("debug_mode = true\n")
##        f.write("[LabEnvironments]\n")
##        f.write("rol_base_url = https://rol.example.com\n")
##        f.write("rol_stage_base_url = https://stage.example.com\n")
##    with open(".env", "w") as f:
##        f.write("RH_USERNAME=testuser_env\n")
##        f.write("GENERAL_DEBUG_MODE=false\n") #This should override config.ini
#    cfg = ConfigManager(config_file_path="../config/config.ini", env_file_path=".env")
#  
#    print(f"Default Selenium Driver: {cfg.get('General', 'default_selenium_driver', 'firefox')}")
#    print(f"Debug Mode (from env): {cfg.get('General', 'debug_mode')}") # Should be False (bool)
#    print(f"RH Username (from env): {cfg.get('Credentials', 'rh_username', 'default_user')}") # Direct key
#    print(f"ROL Base URL: {cfg.get_lab_base_url('rol')}")
#    print(f"Factory Base URL: {cfg.get_lab_base_url('factory')}")
#    print(f"Non Existent: {cfg.get('General', 'non_existent_key', 'fallback')}")
#    # Clean up dummy files
#    # os.remove("config/config.ini")
#    # os.remove(".env")
#    # os.rmdir("config") 