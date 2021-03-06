import argparse
import json
import sys
import time

import requests
from requests import Timeout

from Entities.Fragmenter import Fragmenter
from Entities.Sigfox import Sigfox
from Messages.ACK import ACK
from Messages.Fragment import Fragment
from Messages.SenderAbort import SenderAbort
from config import config

filename = config.MESSAGE
seqNumber = 1
device = "DEVICE"

sent = 0
received = 0
retransmitted = 0


def post(fragment_sent, retransmit=False):
    global seqNumber, attempts, current_window, last_window, i, sent, received, retransmitted
    headers = {'content-type': 'application/json'}
    profile = fragment_sent.profile

    if fragment_sent.is_all_0() and not retransmit:
        print("[POST] This is an All-0. Using All-0 SIGFOX_DL_TIMEOUT.")
        request_timeout = profile.SIGFOX_DL_TIMEOUT
    elif fragment_sent.is_all_1():
        print("[POST] This is an All-1. Using RETRANSMISSION_TIMER_VALUE. Increasing ACK attempts.")
        attempts += 1
        request_timeout = profile.RETRANSMISSION_TIMER_VALUE
    else:
        request_timeout = 45

    payload_dict = {
        "deviceType": "WYSCHC",
        "device": device,
        "time": str(int(time.time())),
        "data": fragment_sent.hex,
        "seqNumber": str(seqNumber),
        "ack": fragment_sent.expects_ack() and not retransmit
    }

    print(f"[POST] Posting fragment {fragment_sent.header.string} ({fragment_sent.hex}) to {SCHC_POST_URL}")

    try:
        response = requests.post(SCHC_POST_URL, data=json.dumps(payload_dict), headers=headers, timeout=request_timeout)

        if fragment_sent.is_sender_abort():
            print("Sent Sender-Abort. Goodbye")
            exit(1)

        seqNumber += 1
        sent += 1
        if retransmit:
            retransmitted += 1
        print(f"[POST] Response: {response}")
        http_code = response.status_code

        # If 500, exit with an error
        if http_code == 500:
            print("Response: 500 Internal Server Error")
            exit(1)

        # If 204, the fragment was posted successfully
        elif http_code == 204:
            print("Response: 204 No Content")
            if fragment_sent.is_all_0() and not retransmit:
                print("Faking timeout")
                time.sleep(profile.SIGFOX_DL_TIMEOUT)
                raise Timeout
            if not retransmit:
                i += 1
            return

        # If 200, the fragment was posted and an ACK has been received.
        elif http_code == 200:
            print(f"Response: 200 OK, Text: {response.text}. Ressetting attempts counter to 0.")
            received += 1
            attempts = 0
            ack = response.json()[device]["downlinkData"]

            # Parse ACK
            ack_object = ACK.parse_from_hex(profile_uplink, ack)

            if ack_object.is_receiver_abort():
                print("ERROR: Receiver Abort received. Aborting communication.")
                exit(1)

            if not fragment_sent.expects_ack():
                print(f"ERROR: ACK received but not requested ({ack}).")
                exit(1)

            # Extract data from ACK
            ack_window = ack_object.w
            ack_window_number = ack_object.window_number
            c = ack_object.c
            bitmap = ack_object.bitmap
            print(f"ACK: {ack}")
            print(f"ACK window: {str(ack_window)}")
            print(f"ACK bitmap: {bitmap}")
            print(f"ACK C bit: {c}")
            print(f"last window: {last_window}")

            # If the W field in the SCHC ACK corresponds to the last window of the SCHC Packet:
            if ack_window_number == last_window:
                # If the C bit is set, the sender MAY exit successfully.
                if c == '1':
                    print("Last ACK received, fragments reassembled successfully. End of transmission.")
                    print(f"TOTAL UPLINK: {sent} ({retransmitted} retransmisiones)")
                    print(f"TOTAL DOWNLINK: {received}")
                    exit(0)
                # Otherwise,
                else:
                    # If the Profile mandates that the last tile be sent in an All-1 SCHC Fragment
                    # (we are in the last window), .is_all_1() should be true:
                    if fragment_sent.is_all_1():
                        # This is the last bitmap, it contains the data up to the All-1 fragment.
                        last_bitmap = bitmap[:len(fragment_list) % window_size]
                        print(f"last bitmap {last_bitmap}")

                        # If the SCHC ACK shows no missing tile at the receiver, abort.
                        # (C = 0 but transmission complete)
                        if last_bitmap[0] == '1' and all(last_bitmap):
                            print("ERROR: SCHC ACK shows no missing tile at the receiver.")
                            post(SenderAbort(fragment_sent.profile, fragment_sent.header))

                        # Otherwise (fragments are lost),
                        else:
                            # Check for lost fragments.
                            for j in range(len(last_bitmap)):
                                # If the j-th bit of the bitmap is 0, then the j-th fragment was lost.
                                if last_bitmap[j] == '0':
                                    print(
                                        f"The {j}th ({window_size * ack_window_number + j} / {len(fragment_list)}) fragment was lost! Sending again...")
                                    # Try sending again the lost fragment.
                                    fragment_to_be_resent = Fragment(profile_uplink,
                                                                     fragment_list[window_size * ack_window + j])
                                    print(f"Lost fragment: {fragment_to_be_resent.string}")
                                    post(fragment_to_be_resent, retransmit=True)

                            # Send All-1 again to end communication.
                            post(fragment_sent)

                    else:
                        print("ERROR: While being at the last window, the ACK-REQ was not an All-1."
                              "This is outside of the Sigfox scope.")
                        exit(1)

            # Otherwise, there are lost fragments in a non-final window.
            else:
                # Check for lost fragments.
                for j in range(len(bitmap)):
                    # If the j-th bit of the bitmap is 0, then the j-th fragment was lost.
                    if bitmap[j] == '0':
                        print(
                            f"The {j}th ({window_size * ack_window_number + j} / {len(fragment_list)}) fragment was lost! Sending again...")
                        # Try sending again the lost fragment.
                        fragment_to_be_resent = Fragment(profile_uplink,
                                                         fragment_list[window_size * ack_window_number + j])
                        print(f"Lost fragment: {fragment_to_be_resent.string}")
                        post(fragment_to_be_resent, retransmit=True)
                if fragment_sent.is_all_1():
                    # Send All-1 again to end communication.
                    post(fragment_sent)
                elif fragment_sent.is_all_0():
                    i += 1
                    current_window += 1

    # If the timer expires
    except Timeout:
        # If an ACK was expected
        if fragment_sent.is_all_1():
            # If the attempts counter is strictly less than MAX_ACK_REQUESTS, try again
            if attempts < profile_uplink.MAX_ACK_REQUESTS:
                print("SCHC Timeout reached while waiting for an ACK. Sending the ACK Request again...")
                post(fragment_sent)
            # Else, exit with an error.
            else:
                print("ERROR: MAX_ACK_REQUESTS reached. Sending Sender-Abort.")
                header = fragment_sent.header
                abort = SenderAbort(profile, header)
                post(abort)

        # If the ACK can be not sent (Sigfox only)
        if fragment_sent.is_all_0():
            print("All-0 timeout reached. Proceeding to next window.")
            if not retransmit:
                i += 1
                current_window += 1

        # Else, HTTP communication failed.
        else:
            print("ERROR: HTTP Timeout reached.")
            exit(1)


# Read the file to be sent.
with open(filename, "rb") as data:
    f = data.read()
    message = bytearray(f)

# Initialize variables.
total_size = len(message)
current_size = 0
percent = round(0, 2)
i = 0
current_window = 0
header_bytes = 1 if total_size <= 300 else 2
profile_uplink = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
profile_downlink = Sigfox("DOWNLINK", "NO ACK", header_bytes)
window_size = profile_uplink.WINDOW_SIZE

parser = argparse.ArgumentParser()
parser.add_argument('--mode', type=str, help="For 'local' or 'cloud' testing.")
parser.add_argument('--clean', action='store_true', help="If set, cleans the Cloud Storage bucket before execution.")
parser.add_argument('--cleanonly', action='store_true', help="If sets, cleans the bucket and exits immediately.")
args = parser.parse_args()

if args.mode == 'cloud':
    SCHC_POST_URL = "https://us-central1-wyschc-niclabs.cloudfunctions.net/schc_receiver"
    REASSEMBLER_URL = "https://us-central1-wyschc-niclabs.cloudfunctions.net/reassemble"
    CLEANUP_URL = "https://us-central1-wyschc-niclabs.cloudfunctions.net/cleanup"

elif args.mode == 'local':
    SCHC_POST_URL = "https://localhost:5000/schc_receiver"
    REASSEMBLER_URL = "https://localhost:5000/reassembler"
    CLEANUP_URL = "https://localhost:5000/cleanup"

if args.clean or args.cleanonly:
    _ = requests.post(url=CLEANUP_URL,
                      json={"header_bytes": header_bytes})
    if args.cleanonly:
        exit(0)

# Fragment the file.
fragmenter = Fragmenter(profile_uplink, message)
fragment_list = fragmenter.fragment()
last_window = (len(fragment_list) - 1) // window_size

# The fragment sender MUST initialize the Attempts counter to 0 for that Rule ID and DTag value pair
# (a whole SCHC packet)
attempts = 0
fragment = None

if len(fragment_list) > (2 ** profile_uplink.M) * window_size:
    print(len(fragment_list))
    print((2 ** profile_uplink.M) * window_size)
    print("ERROR: The SCHC packet cannot be fragmented in 2 ** M * WINDOW_SIZE fragments or less. A Rule ID cannot be "
          "selected.")
    exit(1)

# Start sending fragments.
while i < len(fragment_list):
    # A fragment has the format "fragment = [header, payload]".
    data = bytes(fragment_list[i][0] + fragment_list[i][1])
    current_size += len(fragment_list[i][1])
    percent = round(float(current_size) / float(total_size) * 100, 2)

    # Convert to a Fragment class for easier manipulation.
    resent = None
    timeout = False
    fragment = Fragment(profile_uplink, fragment_list[i])

    # Send the data.
    print("Sending...")

    # On All-0 fragments, this function will wait for SIGFOX_DL_TIMER to expire
    # On All-1 fragments, this function will enter retransmission phase.
    post(fragment)
