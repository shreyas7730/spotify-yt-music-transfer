import ytmusicapi

import os


def setup_ytmusic_with_raw_headers(
    input_file="raw_headers.txt", credentials_file="browser.json"
):
    """
    Loads raw headers from a file and sets up YTMusic connection using ytmusicapi.setup.

    Parameters:
        input_file (str): Path to the file containing raw headers.
        credentials_file (str): Path to save the configuration headers (credentials).

    Returns:
        str: Configuration headers string returned by ytmusicapi.setup.
    """
    # Check if the input file exists
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file {input_file} does not exist.")

    # Read the raw headers from the file
    with open(input_file, "r", encoding="utf-8") as file:
        headers_raw = file.read().strip()

    # Check if the user pasted a JSON formatted dictionary or list (e.g. from browser dev tools)
    if (headers_raw.startswith("{") and headers_raw.endswith("}")) or (headers_raw.startswith("[") and headers_raw.endswith("]")):
        try:
            import json
            headers_dict = json.loads(headers_raw)
            # If it's a list (e.g. Chrome HAR or headers list), convert to dict
            if isinstance(headers_dict, list):
                temp_dict = {}
                for entry in headers_dict:
                    if isinstance(entry, dict) and "name" in entry and "value" in entry:
                        temp_dict[entry["name"]] = entry["value"]
                headers_dict = temp_dict
            
            if isinstance(headers_dict, dict):
                reconstructed_lines = []
                for key, val in headers_dict.items():
                    reconstructed_lines.append(f"{key}: {val}")
                headers_raw = "\n".join(reconstructed_lines)
                print("Successfully detected and converted JSON headers to raw HTTP format.")
        except Exception as e:
            print(f"Tried parsing headers as JSON but failed: {e}. Using raw format.")

    # Ensure "Authorization: SAPISIDHASH" exists in raw headers so ytmusicapi identifies it as BROWSER auth
    if "authorization:" not in headers_raw.lower():
        print("Injecting dummy Authorization header to enforce BROWSER authentication mode.")
        headers_raw += "\nAuthorization: SAPISIDHASH dummy"

    # Use ytmusicapi.setup to process headers and save the credentials
    config_headers = ytmusicapi.setup(
        filepath=credentials_file, headers_raw=headers_raw
    )
    print(f"Configuration headers saved to {credentials_file}")
    return config_headers


if __name__ == "__main__":
    try:
        # Specify file paths
        raw_headers_file = "raw_headers.txt"
        credentials_file = "browser.json"

        # Set up YTMusic with raw headers
        print(f"Setting up YTMusic using headers from {raw_headers_file}...")
        setup_ytmusic_with_raw_headers(
            input_file=raw_headers_file, credentials_file=credentials_file
        )

        print("YTMusic setup completed successfully!")

    except Exception as e:
        print(f"An error occurred: {e}")
