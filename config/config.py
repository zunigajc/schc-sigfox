# Configuration variables for SCHCFOX
# Change the variables name to your files, then remove track from git
# git rm --cached config/config.py

# Cloud Storage Bucket Name
BUCKET_NAME = 'sigfoxschc'
# BUCKET_NAME = 'wyschc-niclabs'
#
# CLIENT_SECRETS_FILE = './credentials/WySCHC-Niclabs-7a6d6ab0ca2b.json'
CLIENT_SECRETS_FILE = './credentials/schc-sigfox-upc-f573cd86ed0a.json'

# File where we will store authentication credentials after acquiring them.
# CREDENTIALS_FILE = './credentials/WySCHC-Niclabs-7a6d6ab0ca2b.json'
CREDENTIALS_FILE = './credentials/schc-sigfox-upc-f573cd86ed0a.json'

# Loss mask path
# LOSS_MASK = './loss_masks/loss_mask_0.txt'
LOSS_MASK = './loss_masks/loss_mask_all_0_test_2.txt'
LOSS_MASK_MODIFIED = './loss_masks/loss_mask_modified.txt'

# Message to be fragmented
MESSAGE = './comm/example_300.txt'
PAYLOAD = './comm/PAYLOAD.txt'

