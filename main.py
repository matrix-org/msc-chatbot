#!/usr/bin/env python3
"""
A bot for matrix users to interact with and to be reminded about various events involving MSC proposals.
"""

from matrix_client.client import MatrixClient
import toml
import requests
import time
from markdown import markdown

client = None
config = None

def invite_received(room_id, state):
    """Matrix room invite received. Join the room"""
    global client
    time.sleep(3) # Workaround for Synapse#2807
    client.join_room(room_id)

def event_received(event):
    """Matrix event received. Act if it was directed at us"""
    global client
    if event['content']['msgtype'] != 'm.text':
        return

    body = event['content']['body'].strip()
    room_id = event['room_id']
    if body.startswith('mscbot:'):
        command = body[8:].strip()
        print("Received command:", command)
        if command.startswith('show active'):
            response = reply_active_fcps()
        else:
            response = "Unknown command"

        # Send the response
        client.get_rooms()[room_id].send_html(markdown(response), body=response, msgtype="m.notice")

def reply_all_mscs():
    """Returns a formatted reply with all MSCs and their current statuses"""
    # TODO: The mscbot api currently only shows active FCPs
    pass

def reply_all_fcps():
    """Returns a formatted reply with all MSCs with proposed, ongoing or concluded FCPs"""
    # TODO: The mscbot api currently only shows active FCPs
    pass

def reply_active_fcps():
    """Returns a formatted reply with MSCs that are in currently active final comment periods"""
    global client

    # Retrieve up to date fcp metadata
    fcps = retrieve_fcps()

    response = "#Today's Active MSCs\n\n"
    for fcp in fcps:
        issue_num = fcp['issue']['number']
        line = '[%d](https://github.com/%s/pull/%d) - %s - ' % (issue_num, config['github']['repo'], issue_num, fcp['issue']['title'])
        if fcp['fcp']['fcp_start'] != None:
            remaining_days = 5 # TODO: Figure out when FCP ends
            line += 'Ends in %d %s' % (5, 'day' if remaining_days == 1 else 'days') 
        else:
            # Show proposed FCPs and team members who have yet to agree
            # TODO: Show concern count
            reviewers = ', '.join([x[0]['login'] for x in fcp['reviews'] if x[1] == False])
            line += 'Proposed (%s)' % reviewers

        response += line + '\n\n'
    return response

def retrieve_fcps():
    """Retrieve information for current FCPs"""
    global config
    r = requests.get(config['mscbot']['url'] + "/api/all")
    return r.json()

def main():
    global client
    global config

    # Retrieve login information from config file
    with open('config.toml', 'r') as f:
        try:
            config = toml.loads(f.read())
        except Exception as e:
            print('Error reading config file:', e)

    # Login to Matrix and listen for messages
    homeserver = 'https://' + config['matrix']['user_id'].split(':')[-1]
    client = MatrixClient(homeserver, user_id=config['matrix']['user_id'], token=config['matrix']['token'])
    client.add_invite_listener(invite_received)
    client.add_listener(event_received, event_type="m.room.message")
    client.listen_forever()

if __name__ == "__main__":
    main()