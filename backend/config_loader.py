import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# GENERATED KEY - DO NOT EDIT MANUALLY
EMBEDDED_KEY = "xryQTLFkIuIzGWsVQn02Q-h5FDQUw7p2qwotsRl2hZA="

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
            print(f"[config] Failed to decrypt config: {e}")
    else:
        print("[config] Warning: Neither .env nor app_config.bin found.")
