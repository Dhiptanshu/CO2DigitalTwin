import os
from cryptography.fernet import Fernet

def obfuscate():
    env_path = ".env"
    output_bin = "app_config.bin"
    loader_path = "backend/config_loader.py"

    if not os.path.exists(env_path):
        print(f"Error: {env_path} not found.")
        return

    # 1. Generate Key
    key = Fernet.generate_key().decode()
    f = Fernet(key.encode())

    # 2. Read .env
    with open(env_path, "rb") as file:
        env_data = file.read()

    # 3. Encrypt
    encrypted_data = f.encrypt(env_data)

    # 4. Write binary
    with open(output_bin, "wb") as file:
        file.write(encrypted_data)
    
    print(f"SUCCESS: Encrypted {env_path} to {output_bin}")

    # 5. Write loader
    loader_content = f'''import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# GENERATED KEY - DO NOT EDIT MANUALLY
EMBEDDED_KEY = "{key}"

def load_config():
    """
    Try loading .env first.
    If not found, decrypt app_config.bin using EMBEDDED_KEY.
    """
    # 1. Try standard .env
    if os.path.exists(".env"):
        print("[config] Loading .env file found locally.")
        load_dotenv()
        return

    # 2. If no .env, try app_config.bin
    bin_path = "app_config.bin"
    if os.path.exists(bin_path):
        print("[config] No .env found. Loading obfuscated configuration...")
        try:
            f = Fernet(EMBEDDED_KEY.encode())
            with open(bin_path, "rb") as file:
                encrypted_data = file.read()
            
            decrypted_data = f.decrypt(encrypted_data).decode("utf-8")
            
            # Parse lines and set env vars
            import io
            from dotenv import load_dotenv
            
            # We can use load_dotenv with stream
            load_dotenv(stream=io.StringIO(decrypted_data))
            print("[config] Successfully loaded obfuscated config.")
        except Exception as e:
            print(f"[config] Failed to decrypt config: {{e}}")
    else:
        print("[config] Warning: Neither .env nor app_config.bin found.")
'''
    
    with open(loader_path, "w") as f:
        f.write(loader_content)

    print(f"SUCCESS: Generated {loader_path} with embedded key.")

if __name__ == "__main__":
    obfuscate()
