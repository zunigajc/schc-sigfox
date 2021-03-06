import random
import re
import time

import requests
from flask import Flask, request
import os
import json
from flask import abort, g
import time
from datetime import datetime
from google.cloud import storage
from function import *
from blobHelperFunctions import *
from Entities.Fragmenter import Fragmenter

import config.config as config
from Entities.Reassembler import Reassembler
from Entities.Sigfox import Sigfox
from Messages.ACK import ACK
from Messages.Fragment import Fragment
from blobHelperFunctions import *
from function import *
from Messages.ReceiverAbort import ReceiverAbort
from Messages.SenderAbort import SenderAbort

import config.config as config

app = Flask(__name__)

# File where we will store authentication credentials after acquiring them.

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.CLIENT_SECRETS_FILE

# Filename must be a json file starting with a /
filename = '/fragments_stats_v7.0.json'
filename_dir = os.path.dirname(__file__)
save_path = os.path.join(filename_dir, 'stats', 'files', 'server')


def cleanup(BUCKET_NAME, profile):
    try:
        _ = requests.post(url='http://localhost:5000/clean',
                          json={"header_bytes": int(profile.HEADER_LENGTH / 8)}, timeout=1)
    except requests.exceptions.ReadTimeout:
        pass

def save_current_fragment(fragment):
    global filename_dir
    global filename
    global save_path
    print("[Save File]: Saving fragment")
    data = {}
    try:
        print("[Save File]: Searching for file")
        with open(save_path + filename)  as json_file:
            data = json.load(json_file)
        print("[Save File]: File Found")
    except Exception as e:
        print("[Save File]: Creating file because {}".format(e))
        file = open(save_path + filename, 'a+')
        seqNumber = fragment['seqNumber']
        data[seqNumber] = fragment
        file.write(json.dumps(data))
        file.write('')
        file.close()
        return
    # print("data: {}".format(data))
    # print("fragment: {}".format(fragment))
    # print("fragment['seqNumber']: {}".format(fragment['seqNumber']))
    seqNumber = fragment['seqNumber']
    data[seqNumber] = fragment
    # print("data: {}".format(data))
    file = open(save_path + filename, 'w')
    file.write(json.dumps(data))
    file.write('')
    file.close()
    return


@app.before_request
def before_request():
    g.start = time.time()
    g.current_fragment = {}
    print("datetime: {}".format(datetime.now()))
    if request.endpoint is None:
        print("[before_request]: No request endpoint")
    elif request.endpoint == 'test_link':
        print('[before_request]: ' + request.endpoint)
        if request.method == 'POST':
            request_dict = request.get_json()
            g.current_fragment['timestamp'] = '{}'.format(datetime.now())
            print('[before_request]: Received Sigfox message: {}'.format(request_dict))
            # Get data and Sigfox Sequence Number.
            fragment = request_dict["data"]
            sigfox_sequence_number = request_dict["seqNumber"]
            device = request_dict['device']
            # data = ''.join("{:08b}".format(str(byte)) for byte in bytes(fragment))
            data = bytearray.fromhex(fragment).decode()
            print('fragment: {}'.format(fragment))
            print('data: {}'.format(data))
            print('[before_request]: Data received from device id:{}, data:{}, sigfox_sequence_number:{}'.format(device,
                                                                 request_dict['data'], sigfox_sequence_number))
            g.current_fragment['s-downlink_enable'] = request_dict['ack']
            g.current_fragment['s-sending_start'] = g.start
            g.current_fragment['s-data'] = data
            g.current_fragment['seqNumber'] = sigfox_sequence_number
            g.current_fragment['s-fragment_size'] = len(data)
            print('[before_request]: {}'.format(g.current_fragment))

    elif request.endpoint == 'wyschc_get':
        print('[before_request]: ' + request.endpoint)
        if request.method == 'POST':
            print("[before_request]: POST RECEIVED")
            # BUCKET_NAME = config.BUCKET_NAME
            request_dict = request.get_json()
            print('[before_request]: Received Sigfox message: {}'.format(request_dict))
            # Get data and Sigfox Sequence Number.
            fragment = request_dict["data"]
            sigfox_sequence_number = request_dict["seqNumber"]
            device = request_dict['device']
            print('[before_request]: Data received from device id:{}, data:{}'.format(device, request_dict['data']))
            # Parse fragment into "fragment = [header, payload]
            header_bytes = None
            header_first_hex = fragment[:1]
            if (header_first_hex) == '0' or (header_first_hex) == '1':
                header = bytes.fromhex(fragment[:2])
                payload = bytearray.fromhex(fragment[2:])
                header_bytes = 1
            elif (header_first_hex) == '2':
                header = bytearray.fromhex(fragment[:4])
                payload = bytearray.fromhex(fragment[4:])
                header_bytes = 2
            else:
                print("[before_request]: Wrong header in fragment")
                return 'wrong header', 204

            data = [header, payload]
            # Initialize SCHC variables.
            profile_uplink = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
            profile_downlink = Sigfox("DOWNLINK", "NO ACK", header_bytes)
            buffer_size = profile_uplink.UPLINK_MTU
            n = profile_uplink.N
            m = profile_uplink.M
            # Convert to a Fragment class for easier manipulation.
            fragment_message = Fragment(profile_uplink, data)

            # Get current window for this fragment.
            current_window = int(fragment_message.header.W, 2)
            # Get some SCHC values from the fragment.
            rule_id = fragment_message.header.RULE_ID
            dtag = fragment_message.header.DTAG
            w = fragment_message.header.W
            g.current_fragment['s-downlink_enable'] = request_dict['ack']
            g.current_fragment['s-sending_start'] = g.start
            g.current_fragment['s-data'] = request_dict["data"]
            g.current_fragment['FCN'] = fragment_message.header.FCN
            g.current_fragment['s-fragment_size'] = len(request_dict["data"])
            g.current_fragment['RULE_ID'] = fragment_message.header.RULE_ID
            g.current_fragment['W'] = fragment_message.header.W
            g.current_fragment['seqNumber'] = sigfox_sequence_number
            print('[before_request]: seqNum:{}, RULE_ID: {} W: {}, FCN: {}'.format(sigfox_sequence_number,
                                                                                   fragment_message.header.RULE_ID,
                                                                                   fragment_message.header.W,
                                                                                   fragment_message.header.FCN))
            print('[before_request]: {}'.format(g.current_fragment))


@app.after_request
def after_request(response):
    diff = time.time() - g.start
    print("[after_request]: execution time: {}".format(diff))
    if request.endpoint == 'wyschc_get':
        g.current_fragment['s-sending_end'] = time.time()
        g.current_fragment['s-send_time'] = diff
        g.current_fragment['s-lost'] = False
        g.current_fragment['s-ack'] = ''
        g.current_fragment['s-ack_send'] = False
        if response.status_code == 204:
            print("[after_request]: response.status_code == 204")
            print(response.get_data())
            if 'fragment lost' in str(response.get_data()):
                print('[after_request]: ups.. fragment lost')
                g.current_fragment['s-lost'] = True
            if 'Sender-Abort received' in str(response.get_data()):
                print('[after_request]: Sender-Abort received, 204')
                g.current_fragment['s-sender-abort'] = True
                

        if response.status_code == 200:
            print("[after_request]: response.status_code == 200")
            response_dict = json.loads(response.get_data())
            print("[after_request]: response_dict: {}".format(response_dict))
            if 'message' in response_dict:
                print(response_dict['message'])
                if 'Message ignored' in response_dict['message']:
                    g.current_fragment['s-ignored'] = True
            else:
                for device in response_dict:
                    print("[after_request]: {}".format(response_dict[device]['downlinkData']))
                    g.current_fragment['s-ack'] = response_dict[device]['downlinkData']
                    g.current_fragment['s-ack_send'] = True

        print('[after_request]: current fragment:{}'.format(g.current_fragment))
        save_current_fragment(g.current_fragment)
        # ack_received
        # sending_end
        # ack
        # send_time

    elif request.endpoint == 'test_link':
        g.current_fragment['s-sending_end'] = time.time()
        g.current_fragment['s-send_time'] = diff
        g.current_fragment['s-ack'] = ''
        g.current_fragment['s-ack_send'] = False
        if response.status_code == 200:
            print("[after_request]: response.status_code == 200")
            response_dict = json.loads(response.get_data())
            print("[after_request]: response_dict: {}".format(response_dict))
            for device in response_dict:
                print("[after_request]: {}".format(response_dict[device]['downlinkData']))
                g.current_fragment['s-ack'] = response_dict[device]['downlinkData']
                g.current_fragment['s-ack_send'] = True
        print('[after_request]: current fragment:{}'.format(g.current_fragment))
        save_current_fragment(g.current_fragment)

    return response


@app.route('/')
def hello_world():
    return 'Hello, World!'


@app.route('/test_link', methods=['POST'])
def test_link():
    """Responds to any HTTP request.
    Args:
        request (flask.Request): HTTP request object.
    Returns:
        The response text or any set of values that can be turned into a
        Response object using
        `make_response <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>`.
    """
    import json
    request_json = request.get_json()

    # if request.args and 'device' in request.args:
    #    return request.args.get('message')
    if request_json and 'device' in request_json and 'data' in request_json:
        device = request_json['device']
        print('Data received from device id:{}, data:{}'.format(device, request_json['data']))
        if 'ack' in request_json:
            if request_json['ack'] == 'true':
                response = {request_json['device']: {'downlinkData': '07f7ffffffffffff'}}
                print("response -> {}".format(response))
                return json.dumps(response), 200
        return '', 204
    else:
        return f'Not a correct format message', 404



@app.route('/test', methods=['POST'])
def test():
    """Responds to any HTTP request.
    Args:
        request (flask.Request): HTTP request object.
    Returns:
        The response text or any set of values that can be turned into a
        Response object using
        `make_response <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>`.
    """
    import json
    request_json = request.get_json()

    # if request.args and 'device' in request.args:
    #    return request.args.get('message')
    if request_json and 'device' in request_json and 'data' in request_json:
        device = request_json['device']
        print('Data received from device id:{}, data:{}'.format(device, request_json['data']))
        if 'ack' in request_json:
            if request_json['ack'] == 'true':
                response = {request_json['device']: {'downlinkData': '07f7ffffffffffff'}}
                print("response -> {}".format(response))
                return json.dumps(response), 200
        return '', 204
    else:
        return f'Not a correct format message', 404


global message_number
counter_w0 = 0
counter_w1 = 0


@app.route('/post/message', methods=['GET', 'POST'])
def post_message():
    global counter_w0
    global counter_w1

    if request.method == 'POST':
        print("POST RECEIVED")
        # Get request JSON.
        print("[SCHC-SIGFOX]: POST RECEIVED")
        request_dict = request.get_json()
        print('Received Sigfox message: {}'.format(request_dict))

        # Get data and Sigfox Sequence Number.
        fragment = request_dict["data"]
        sigfox_sequence_number = request_dict["seqNumber"]
        device = request_dict['device']
        print('Data received from device id:{}, data:{}'.format(device, request_dict['data']))
        # Parse fragment into "fragment = [header, payload]
        header_bytes = None
        header_first_hex = fragment[:1]
        if (header_first_hex) == '0' or (header_first_hex) == '1':
            header = bytes.fromhex(fragment[:2])
            payload = bytearray.fromhex(fragment[2:])
            header_bytes = 1
        elif (header_first_hex) == '2':
            header = bytearray.fromhex(fragment[:4])
            payload = bytearray.fromhex(fragment[4:])
            header_bytes = 2
        else:
            print("Wrong header in fragment")
            return 'wrong header', 204
        print('payload: {}'.format(payload))
        data = [header, payload]
        # Initialize SCHC variables.
        profile_uplink = Sigfox("UPLINK", "ACK ON ERROR")
        profile_downlink = Sigfox("DOWNLINK", "NO ACK")
        buffer_size = profile_uplink.UPLINK_MTU
        n = profile_uplink.N
        m = profile_uplink.M
        # Convert to a Fragment class for easier manipulation.
        fragment_message = Fragment(profile_uplink, data)
        # Get some SCHC values from the fragment.
        rule_id = fragment_message.header.RULE_ID
        dtag = fragment_message.header.DTAG
        w = fragment_message.header.W
        print('RULE_ID: {} W: {}, FCN: {}'.format(fragment_message.header.RULE_ID,fragment_message.header.W, fragment_message.header.FCN))
        if fragment_message.is_sender_abort():
            print('sender abort found')
        else:
            print("no sender abort message found")
        if 'ack' in request_dict:
            if request_dict['ack'] == 'true':
                print("sending Receiver Abort message")
                w = ''
                while len(w) < profile_uplink.M:
                    w += '1'
                print("w: {}".format(w))
                abortMessage = ReceiverAbort(profile_downlink, rule_id, dtag)
                print(abortMessage.to_string())
                response_json = send_ack(request_dict, abortMessage)
                # receiverAbort = '0001111111111111111111111111111111111111111111111111111111111111'
                # response = {request_dict['device']: {'downlinkData': receiverAbort}}
                print("response -> {}".format(response_json))
                return response_json, 200

                print('w:{}'.format(w))
                if w == '00':
                    # print('ACK already send for this window, move along')
                    # counter_w0 = 0
                    # return '', 204
                    if counter_w0 == 1:
                        # print('ACK already send for this window, move along')
                        print("This time send an ACK for window 1")
                        counter_w0 = 0
                        bitmap = '0000001'
                        ack = ACK(profile_downlink, rule_id, dtag, "01", bitmap, '0')
                        response_json = send_ack(request_dict, ack)
                        print("200, Response content -> {}".format(response_json))
                        return 'fragment lost', 204
                    counter_w0 += 1
                    print('lets say we lost the All-0, so move along')
                    return 'fragment lost', 204
                    # return str(counter)
                    # Create an ACK message and send it.
                    bitmap = '1011111'
                    bitmap = '1000000'
                    bitmap = '0100001'
                    ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '1')
                    response_json = send_ack(request_dict, ack)
                    print("200, Response content -> {}".format(response_json))
                    # response = {request_dict['device']: {'downlinkData': '07f7ffffffffffff'}}
                    # print("response -> {}".format(response))
                    return response_json, 200
                elif w == '01':
                    if counter_w1 == 1:

                        print("This time send an ACK for window 1")
                        # counter_w0 = 0
                        counter_w1 += 1
                        bitmap = '0000001'
                        ack = ACK(profile_downlink, rule_id, dtag, "01", bitmap, '0')
                        response_json = send_ack(request_dict, ack)
                        print("200, Response content -> {}".format(response_json))
                        return '', 204

                    elif counter_w1 == 2:
                        print('Resend an ACK for window 1')
                        counter_w1 += 1
                        bitmap = '0000001'
                        ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '0')
                        response_json = send_ack(request_dict, ack)
                        print("200, Response content -> {}".format(response_json))
                        # response = {request_dict['device']: {'downlinkData': '07f7ffffffffffff'}}
                        # print("response -> {}".format(response))
                        return response_json, 200

                    elif counter_w1 == 3:
                        print('ACK already send for this window, send last ACK')
                        counter_w1 = 0
                        bitmap = '0100001'
                        ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '1')
                        response_json = send_ack(request_dict, ack)
                        print("200, Response content -> {}".format(response_json))
                        # response = {request_dict['device']: {'downlinkData': '07f7ffffffffffff'}}
                        # print("response -> {}".format(response))
                        return response_json, 200

                        bitmap = '0100001'
                        ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '1')
                        response_json = send_ack(request_dict, ack)
                        print("200, Response content -> {}".format(response_json))
                    counter_w1 += 1
                    # Create an ACK message and send it.
                    bitmap = '0000001'

                    ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '0')

                    # Test for loss of All-0 in window 0
                    bitmap = '1010110'
                    ack = ACK(profile_downlink, rule_id, dtag, '00', bitmap, '0')
                    # ack = ACK(profile_downlink, rule_id, dtag, w, bitmap, '1')
                    response_json = send_ack(request_dict, ack)
                    print("200, Response content -> {}".format(response_json))
                    # response = {request_dict['device']: {'downlinkData': '07f7ffffffffffff'}}
                    # print("response -> {}".format(response))
                    return response_json, 200
                else:
                    return '', 204
            return '', 204
        else:
            return f'Not a correct format message', 404


@app.route('/hello_get', methods=['GET', 'POST'])
def hello_get():
    """HTTP Cloud Function.
    Args:
        request (flask.Request): The request object.
        <http://flask.pocoo.org/docs/1.0/api/#flask.Request>
    Returns:
        The response text, or any set of values that can be turned into a
        Response object using `make_response`
        <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>.
    """

    # Wait for an HTTP POST request.
    if request.method == 'POST':
        # Get request JSON.
        print("POST RECEIVED")
        request_dict = request.get_json()
        print('Received Sigfox message: {}'.format(request_dict))

        # Get data and Sigfox Sequence Number.
        fragment = request_dict["data"]
        sigfox_sequence_number = request_dict["seqNumber"]


@app.route('/clean', methods=['GET', 'POST'])
def clean():
    """HTTP Cloud Function.
    Args:
        request (flask.Request): The request object.
        <http://flask.pocoo.org/docs/1.0/api/#flask.Request>
    Returns:
        The response text, or any set of values that can be turned into a
        Response object using `make_response`
        <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>.
    """

    # Wait for an HTTP POST request.
    if request.method == 'POST':

        # Get request JSON.
        # print("[Clean]: POST RECEIVED")
        # BUCKET_NAME = config.BUCKET_NAME
        # request_dict = request.get_json()
        # print('[Clean]: Received Request message: {}'.format(request_dict))
        # header_bytes = int(request_dict["header_bytes"])
        # profile = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
        # bitmap = ''
        # for i in range(2 ** profile.N - 1):
        #     bitmap += '0'
        # for i in range(2 ** profile.M):
        #     upload_blob(BUCKET_NAME, bitmap, "all_windows/window_%d/bitmap_%d" % (i, i))
        #     upload_blob(BUCKET_NAME, bitmap, "all_windows/window_%d/losses_mask_%d" % (i, i))
        # print("[Clean]: Clean completed")

        header_bytes = request.get_json()["header_bytes"]
        profile = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)

        print("[CLN] Deleting timestamp blob")
        delete_blob(config.BUCKET_NAME, "timestamp")

        print("[CLN] Resetting SSN")
        upload_blob(config.BUCKET_NAME, "{}", "SSN")

        print("[CLN] Initializing fragments...")
        delete_blob(config.BUCKET_NAME, "all_windows/")
        initialize_blobs(config.BUCKET_NAME, profile)
        return '', 204

@app.route('/losses_mask', methods=['GET', 'POST'])
def losses_mask():
    """HTTP Cloud Function.
    Args:
        request (flask.Request): The request object.
        <http://flask.pocoo.org/docs/1.0/api/#flask.Request>
    Returns:
        The response text, or any set of values that can be turned into a
        Response object using `make_response`
        <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>.
    """

    # Wait for an HTTP POST request.
    if request.method == 'POST':

        # Get request JSON.
        print("POST RECEIVED")
        BUCKET_NAME = config.BUCKET_NAME
        request_dict = request.get_json()
        print('Received Request message: {}'.format(request_dict))
        mask = request_dict["mask"]
        header_bytes = int(request_dict["header_bytes"])
        profile = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
        for i in range(2 ** profile.M):
            upload_blob(BUCKET_NAME, mask, "all_windows/window_%d/losses_mask_%d" % (i, i))

        return '', 204

@app.route('/wyschc_get', methods=['GET', 'POST'])
def wyschc_get():
    """HTTP Cloud Function.
    Args:
        request (flask.Request): The request object.
        <http://flask.pocoo.org/docs/1.0/api/#flask.Request>
    Returns:
        The response text, or any set of values that can be turned into a
        Response object using `make_response`
        <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>.
    """

    # Wait for an HTTP POST request.
    if request.method == 'POST':

        # Get request JSON.
        print("[SCHC-SIGFOX]: POST RECEIVED")
        request_dict = request.get_json()
        print('Received Sigfox message: {}'.format(request_dict))

        # Get data and Sigfox Sequence Number.
        raw_data = request_dict["data"]
        sigfox_sequence_number = request_dict["seqNumber"]

        # Initialize Cloud Storage variables.
        BUCKET_NAME = config.BUCKET_NAME

        header_first_hex = raw_data[:1]
        if header_first_hex == '0' or header_first_hex == '1':
            header = bytes.fromhex(raw_data[:2])
            payload = bytearray.fromhex(raw_data[2:])
            header_bytes = 1
        elif header_first_hex == '2':
            header = bytearray.fromhex(raw_data[:4])
            payload = bytearray.fromhex(raw_data[4:])
            header_bytes = 2
        else:
            print("Wrong header in raw_data")
            return 'wrong header', 204

        # Initialize SCHC variables.
        profile = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
        n = profile.N
        m = profile.M

        # If fragment size is greater than buffer size, ignore it and end function.
        if len(raw_data) / 2 * 8 > profile.UPLINK_MTU:  # Fragment is hex, 1 hex = 1/2 byte
            return json.dumps({"message": "Fragment size is greater than buffer size"}), 200

        # If the folder named "all windows" does not exist, create it along with all subdirectories.
        initialize_blobs(BUCKET_NAME, profile)

        # Initialize empty window
        window = []
        for i in range(2 ** n - 1):
            window.append([b"", b""])

        # Compute the fragment compressed number (FCN) from the Profile
        fcn_dict = {}
        for j in range(2 ** n - 1):
            fcn_dict[zfill(bin((2 ** n - 2) - (j % (2 ** n - 1)))[2:], 3)] = j
        print("{}".format(fcn_dict))
        # Parse raw_data into "data = [header, payload]
        # Convert to a Fragment class for easier manipulation.
        header = bytes.fromhex(raw_data[:2])
        payload = bytearray.fromhex(raw_data[2:])
        data = [header, payload]
        fragment_message = Fragment(profile, data)

        if fragment_message.is_sender_abort():
            cleanup(BUCKET_NAME, profile)
            return 'Sender-Abort received', 204

        # Get data from this fragment.
        fcn = fragment_message.header.FCN
        rule_id = fragment_message.header.RULE_ID
        dtag = fragment_message.header.DTAG
        current_window = int(fragment_message.header.W, 2)

        # Get the current bitmap.
        bitmap = read_blob(BUCKET_NAME, f"all_windows/window_{current_window}/bitmap_{current_window}")

        # Controlling deterministic losses. This loads the file "loss_mask.txt" which states when should a fragment be
        # lost, separated by windows.
        fd = None
        try:
            fd = open(config.LOSS_MASK_MODIFIED, "r")
        except FileNotFoundError:
            print('opening: {}'.format(config.LOSS_MASK))
            fd = open(config.LOSS_MASK, "r")
        finally:
            loss_mask = []
            for line in fd:
                if not line.startswith("#"):
                    for char in line:
                        try:
                            loss_mask.append(int(char))
                        except ValueError:
                            pass
            fd.close()

        print(f"Loss mask: {loss_mask}")

        # Controlling random losses.
        if 'enable_losses' in request_dict and not (fragment_message.is_all_0() or fragment_message.is_all_1()):
            if request_dict['enable_losses']:
                loss_rate = request_dict["loss_rate"]
                # loss_rate = 10
                coin = random.random()
                print(f'loss rate: {loss_rate}, random toss:{coin * 100}')
                if coin * 100 < loss_rate:
                    print("[LOSS] The fragment was lost.")
                    return 'fragment lost', 204

        # Inactivity timer validation
        time_received = int(request_dict["time"])

        if exists_blob(BUCKET_NAME, "timestamp"):
            # Check time validation.
            last_time_received = int(read_blob(BUCKET_NAME, "timestamp"))
            print(f"[RECV] Previous timestamp: {last_time_received}")
            print(f"[RECV] This timestamp: {time_received}")

            # If the inactivity timer has been reached, abort communication.
            if time_received - last_time_received > profile.INACTIVITY_TIMER_VALUE:
                print("[RECV] Inactivity timer reached. Ending session. {}".format(time_received - last_time_received))
                receiver_abort = ReceiverAbort(profile, fragment_message.header)
                print("Sending Receiver Abort")
                response_json = send_ack(request_dict, receiver_abort)
                print(f"Response content -> {response_json}")
                cleanup(BUCKET_NAME, profile)
                return response_json, 200

        # Upload timestamp
        upload_blob(BUCKET_NAME, time_received, "timestamp")

        # Check if the fragment is an All-1
        if is_monochar(fcn) and fcn[0] == '1':
            print("[RECV] This is an All-1.")

            # Check if fragment is to be lost (All-1 is the very last fragment)
            if loss_mask[-1] != 0:
                # CHECK THIS: loss_mask [-1] is not always the last fragment............
                loss_mask[-1] -= 1
                with open(config.LOSS_MASK_MODIFIED, "w") as fd:
                    for i in loss_mask:
                        fd.write(str(i))
                return 'fragment lost', 204

            # Update bitmap and upload it.
            bitmap = replace_bit(bitmap, len(bitmap) - 1, '1')
            upload_blob_using_threads(BUCKET_NAME,
                                      bitmap,
                                      f"all_windows/window_{current_window}/bitmap_{current_window}")

        # Else, it is a normal fragment.
        else:
            fragment_number = fcn_dict[fragment_message.header.FCN]

            # Check if fragment is to be lost
            position = current_window * profile.WINDOW_SIZE + fragment_number
            if loss_mask[position] != 0:
                loss_mask[position] -= 1
                with open(config.LOSS_MASK_MODIFIED, "w") as fd:
                    for i in loss_mask:
                        fd.write(str(i))
                print(f"Loss mask after loss: {loss_mask}")
                return 'fragment lost', 204

            upload_blob(BUCKET_NAME, fragment_number, "fragment_number")

            # Print some data for the user.
            print(f"[RECV] This corresponds to the {str(fragment_number)}th fragment "
                  f"of the {str(current_window)}th window.")
            print(f"[RECV] Sigfox sequence number: {str(sigfox_sequence_number)}")

            # Controlled Errors check
            # losses_mask = read_blob(BUCKET_NAME,
            #                         "all_windows/window_%d/losses_mask_%d" % (current_window, current_window))
            # if (losses_mask[fragment_number]) != '0':
            #     losses_mask = replace_bit(losses_mask, fragment_number, str(int(losses_mask[fragment_number]) - 1))
            #     upload_blob(BUCKET_NAME, losses_mask,
            #                 "all_windows/window_%d/losses_mask_%d" % (current_window, current_window))
            #     print("[LOSS] The fragment was lost.")
            #     return 'fragment lost', 204

            # Update bitmap and upload it.
            bitmap = replace_bit(bitmap, fragment_number, '1')
            upload_blob(BUCKET_NAME, bitmap, f"all_windows/window_{current_window}/bitmap_{current_window}")

            # Upload the fragment data.
            upload_blob_using_threads(BUCKET_NAME, data[0].decode("utf-8") + data[1].decode("utf-8"),
                                      f"all_windows/window_{current_window}/fragment_{current_window}_{fragment_number}")

        # Get last and current Sigfox sequence number (SSN)
        last_sequence_number = 0
        if exists_blob(BUCKET_NAME, "SSN"):
            last_sequence_number = read_blob(BUCKET_NAME, "SSN")
        upload_blob(BUCKET_NAME, sigfox_sequence_number, "SSN")

        # If the fragment is at the end of a window (ALL-0 or ALL-1) it expects an ACK.
        if fragment_message.expects_ack():

            # Prepare the ACK bitmap. Find the first bitmap with a 0 in it.
            # This bitmap corresponds to the lowest-numered window with losses.
            bitmap_ack = None
            window_ack = None
            for i in range(current_window+1):
                bitmap_ack = read_blob(BUCKET_NAME, f"all_windows/window_{i}/bitmap_{i}")
                print(bitmap_ack)
                window_ack = i
                if '0' in bitmap_ack:
                    break

            # The final window is only accessible through All-1.
            # If All-0, check non-final windows
            if fragment_message.is_all_0():
                # If the ACK bitmap has a 0 at a non-final window, a fragment has been lost.
                if '0' in bitmap_ack:
                    print("[ALL0] Lost fragments have been detected. Preparing ACK.")
                    print(f"[ALL0] Bitmap with errors -> {bitmap_ack}")
                    ack = ACK(profile=profile,
                              rule_id=rule_id,
                              dtag=dtag,
                              w=zfill(format(window_ack, 'b'), m),
                              c='0',
                              bitmap=bitmap_ack)
                    response_json = send_ack(request_dict, ack)
                    print(f"Response content -> {response_json}")
                    print("[ALL0] ACK sent.")
                    return response_json, 200
                # If all bitmaps are complete up to this point, no losses are detected.
                else:
                    print("[ALL0] No losses have been detected.")
                    print("Response content -> ''")
                    return '', 204

            # If the fragment is All-1, the last window should be considered.
            if fragment_message.is_all_1():
                # First check for 0s in the bitmap. If the bitmap is of a non-final window, send corresponding ACK.
                if '0' in bitmap_ack and current_window != window_ack:
                        print("[ALL1] Lost fragments have been detected. Preparing ACK.")
                        print(f"[ALL1] Bitmap with errors -> {bitmap_ack}")
                        ack = ACK(profile=profile,
                                  rule_id=rule_id,
                                  dtag=dtag,
                                  w=zfill(format(window_ack, 'b'), m),
                                  c='0',
                                  bitmap=bitmap_ack)
                        response_json = send_ack(request_dict, ack)
                        print(f"Response content -> {response_json}")
                        print("[ALL1] ACK sent.")
                        return response_json, 200

                # If the bitmap is of the final window, check the following regex.
                else:
                    # The bitmap in the last window follows the following regular expression: "1*0*1*"
                    # Since the ALL-1, if received, changes the least significant bit of the bitmap.
                    # For a "complete" bitmap in the last window, there shouldn't be non-consecutive zeroes:
                    # 1110001 is a valid bitmap, 1101001 is not.
                    # The bitmap may or may not contain the 0s.
                    pattern = re.compile("1*0*1")

                    # If the bitmap matches the regex, check if there are still lost fragments.
                    if pattern.fullmatch(bitmap_ack) is not None:
                        # If the last two received fragments are consecutive (sequence-number-wise),
                        # accept the ALL-1 and start reassembling
                        if int(sigfox_sequence_number) - int(last_sequence_number) == 1:
                            print("[ALL1] Integrity checking complete, launching reassembler.")
                            # All-1 does not define a fragment number, so its fragment number must be the next
                            # of the last registered fragment number.
                            last_index = int(read_blob(BUCKET_NAME, "fragment_number")) + 1
                            upload_blob_using_threads(BUCKET_NAME,
                                                      data[0].decode("ISO-8859-1") + data[1].decode("utf-8"),
                                                      f"all_windows/window_{current_window}/"
                                                      f"fragment_{current_window}_{last_index}")
                            try:
                                _ = requests.post(url='http://localhost:5000/http_reassemble',
                                                  json={"last_index": last_index, "current_window": current_window,
                                                        "header_bytes": header_bytes}, timeout=0.1)
                            except requests.exceptions.ReadTimeout:
                                pass

                            # Send last ACK to end communication (on receiving an All-1, if no fragments are lost,
                            # if it has received at least one tile, return an ACK for the highest numbered window we
                            # currently have tiles for).
                            print("[ALL1] Preparing last ACK")
                            bitmap = ''
                            for k in range(profile.BITMAP_SIZE):
                                bitmap += '0'
                            last_ack = ACK(profile=profile,
                                           rule_id=rule_id,
                                           dtag=dtag,
                                           w=zfill(format(window_ack, 'b'), m),
                                           c='1',
                                           bitmap=bitmap_ack)
                            response_json = send_ack(request_dict, last_ack)
                            # return response_json, 200
                            # response_json = send_ack(request_dict, last_ack)
                            print(f"200, Response content -> {response_json}")
                            print("[ALL1] Last ACK has been sent.")
                            # should not clean here, last ACK is sent, but if it gets lost, then you need to resend
                            # if clean, there is nothing to resend.
                            #cleanup(BUCKET_NAME, profile)
                            return response_json, 200
                    # If the last two fragments are not consecutive, or the bitmap didn't match the regex,
                    # send an ACK reporting losses.
                    else:
                        # Send NACK at the end of the window.
                        print("[ALLX] Sending NACK for lost fragments...")
                        ack = ACK(profile=profile,
                                  rule_id=rule_id,
                                  dtag=dtag,
                                  w=zfill(format(window_ack, 'b'), m),
                                  c='0',
                                  bitmap=bitmap_ack)
                        response_json = send_ack(request_dict, ack)
                        return response_json, 200

        return '', 204

    else:
        print('Invalid HTTP Method to invoke Cloud Function. Only POST supported')
        return abort(405)


@app.route('/http_reassemble', methods=['GET', 'POST'])
def http_reassemble():
    if request.method == "POST":
        print(">>>[RSMB] The reassembler has been launched.")
        # Get request JSON.
        request_dict = request.get_json()
        print(f'>>>[RSMB] Received HTTP message: {request_dict}')

        current_window = int(request_dict["current_window"])
        last_index = int(request_dict["last_index"])
        header_bytes = int(request_dict["header_bytes"])

        # Initialize Cloud Storage variables.
        BUCKET_NAME = config.BUCKET_NAME

        # Initialize SCHC variables.
        profile_uplink = Sigfox("UPLINK", "ACK ON ERROR", header_bytes)
        n = profile_uplink.N

        print(">>>[RSMB] Loading fragments")
        print("[REASSEMBLE] Reassembling...")
        print("[REASSEMBLE] current_window: {}".format(current_window))
        print("[REASSEMBLE] last_index: {}".format(last_index))


        # Get all the fragments into an array in the format "fragment = [header, payload]"
        fragments = []

        # For each window, load every fragment into the fragments array
        for i in range(current_window + 1):
            for j in range(2 ** n - 1):
                print(f">>>[RSMB] Loading fragment {j}")
                fragment_file = read_blob(BUCKET_NAME, f"all_windows/window_{i}/fragment_{i}_{j}")
                print(f">>>[RSMB] Fragment data: {fragment_file}")
                header = fragment_file[:header_bytes]
                payload = fragment_file[header_bytes:]
                fragment = [header.encode(), payload.encode()]
                fragments.append(fragment)
                if i == current_window and j == last_index:
                    break

        # Instantiate a Reassembler and start reassembling.
        print(">>>[RSMB] Reassembling")
        reassembler = Reassembler(profile_uplink, fragments)
        payload = bytearray(reassembler.reassemble()).decode("utf-8")

        print(">>>[RSMB] Uploading result")
        with open(config.PAYLOAD, "w") as file:
            file.write(payload)
        # Upload the full message.
        upload_blob_using_threads(BUCKET_NAME, payload, "PAYLOAD")
        # try:
        #     print("Waiting INACTIVITY_TIMER_VALUE: {} seg for cleaning".format(profile_uplink.INACTIVITY_TIMER_VALUE))
        #     time.sleep(profile_uplink.INACTIVITY_TIMER_VALUE)
        #     _ = requests.post(url='http://localhost:5000/clean',
        #                       json={"header_bytes": header_bytes}, timeout=1)
        # except requests.exceptions.ReadTimeout:
        #     pass
        return '', 204


if __name__ == "__main__":
    app.run(host='0.0.0.0')
