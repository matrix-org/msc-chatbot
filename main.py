#!/usr/bin/env python3
"""
A bot for matrix users to interact with and to be reminded about various events involving MSC proposals.
"""

from matrix_client.client import MatrixClient
from datetime import datetime, timedelta
from markdown import markdown
from github import Github
import schedule
import time
import toml
import requests

client = None
config = None
github = None
repo = None
msc_labels = None

def invite_received(room_id, state):
    """Matrix room invite received. Join the room"""
    global client
    time.sleep(3) # Workaround for Synapse#2807
    try:
        print("Joining room:", room_id)
        client.join_room(room_id)
    except Exception as e:
        print("Unable to join room:", e)
        print("Trying again...")
        time.sleep(5)
        invite_received(room_id, state)

def event_received(event):
    """Matrix event received. Act if it was directed at us"""
    global client
    if event["content"]["msgtype"] != "m.text":
        return

    body = event["content"]["body"].strip()
    room = client.get_rooms()[event["room_id"]]
    user_id = config["matrix"]["user_id"]
    username = user_id[1:user_id.index(":")]
    if body.startswith(username + ":"):
        command = body[8:].strip()
        print("Received command:", command)
        if (not command.startswith("show new") and
           not command.startswith("show pending") and
           not command.startswith("show fcp") and
           not command.startswith("show active")):
            room.send_html("Unknown command", msgtype="m.notice")
            return

        # Retrieve MSC information from Github labels
        mscs = get_mscs()

        try:
            room.send_html("Downloading data...", msgtype="m.notice")
        except Exception as e:
            print("Unable to say 'Downloading data':", e)

        try:
            if command.startswith("show new"):
                response = reply_new_mscs(mscs)
            elif command.startswith("show pending"):
                response = reply_pending_mscs(mscs)
            elif command.startswith("show fcp"):
                response = reply_fcp_mscs(mscs)
            elif command.startswith("show active"):
                response = reply_active_mscs(mscs)

            # Send the response
            print("Sending")
            room.send_html(markdown(response), body=response, msgtype="m.notice")
        except Exception as e:
            print("Unable to post to room:", e)

def send_summary():
    """Sends a daily summary of MSCs to every room the bot is in"""
    # TODO: Ability to turn this on or off per room
    global client

    # Get MSC metadata from Github labels
    mscs = get_mscs()

    info = reply_active_mscs(mscs)
    for room in list(client.get_rooms().values):
        room.send_html(markdown(info), body=info, msgtype="m.notice")

def reply_new_mscs(mscs):
    """Returns a formatted reply with MSCs that are proposed but not yet pending an FCP"""
    new = []
    for msc_dict in mscs:
        msc = msc_dict["issue"]
        labels = msc_dict["labels"]
        if msc_labels["proposal-in-review"] in labels:
            new.append("[[MSC%d](%s)] - %s" % (msc.number, msc.url, msc.title))

    if len(new) > 0:
        response = "\n\n**New**\n\n"
        response += '\n\n'.join(new)
        return response

    return "No new MSCs."

def reply_pending_mscs(mscs):
    """Returns a formatted reply with MSCs that are currently pending a FCP"""
    pending = []
    for msc_dict in mscs:
        msc = msc_dict["issue"]
        labels = msc_dict["labels"]
        fcp = msc_dict["fcp"]
        if msc_labels["proposed-final-comment-period"] in labels and fcp != None:
            # Show proposed FCPs and team members who have yet to agree
            # TODO: Show concern count
            reviewers = ", ".join([x[0]["login"] for x in fcp["reviews"] if x[1] == False])
            line = "[[MSC%d](%s)] - %s - *%s*" % (msc.number, msc.url, msc.title, fcp["fcp"]["disposition"])
            line += "\n\nTo review: %s" % reviewers
            pending.append(line)

    if len(pending) > 0:
        response = "\n\n \n\n**Pending FCP**\n\n"
        response += '\n\n'.join(pending)
        return response

    return "No pending FCPs."

def reply_fcp_mscs(mscs):
    """Returns a formatted reply with all MSCs that are in the FCP"""
    fcps = []
    for msc_dict in mscs:
        msc = msc_dict["issue"]
        labels = msc_dict["labels"]
        if msc_labels["final-comment-period"] in labels:
            # Figure out remaining days in FCP
            # Assume last comment by MSCBot was made when FCP started
            comments = msc.get_comments()
            for comment in list(comments)[::-1]: # Iterate from newest comments
                # Check mscbot user id (retrieve from `curl -A 'mscbot' https://api.github.com/users/mscbot`) 
                if comment.user.id == 40832866 or comment.user.id == 46318632: 
                    time = comment.created_at - timedelta(days=1)

            remaining_days = config["msc"]["fcp_length"] - (datetime.today() - time).days
            line = "[[MSC%d](%s)] - %s" % (msc.number, msc.url, msc.title)
            line += " - Ends in **%d %s**" % (remaining_days, "day" if remaining_days == 1 else "days")
            fcps.append(line)

    if len(fcps) > 0:
        response = "\n\n \n\n**Final Comment Period**\n\n"
        response += '\n\n'.join(fcps)
        return response

    return "No ongoing FCPs."

def reply_active_mscs(mscs):
    """Returns a formatted reply with MSCs that are proposed, pending or in FCP. Used as daily message."""
    global client

    # Sort MSCs by ID
    mscs = sorted(mscs, key=lambda msc: msc["issue"].number)

    # Display active MSCs by status: proposed, fcp pending, and fcp
    response = "# Today's Active MSCs\n\n"
    response += reply_new_mscs(mscs)
    response += reply_pending_mscs(mscs)
    response += reply_fcp_mscs(mscs)
    print(response)
    return response

def get_mscs():
    """Get up to date MSC metadata from Github"""
    global github
    global repo
    global msc_labels

    # Download issues/pulls from github with active MSC labels
    issues = []
    for label in msc_labels.values():
        for issue in repo.get_issues(labels=list([label])):
            issues.append(issue)

    # Create a list relating an issue to its possible FCP information (FCP info added later)
    issues = [({"issue": issue,
                "labels": list(issue.get_labels()),
                "fcp": None}) for issue in issues]

    # Link issues to metadata from MSCBot
    r = requests.get(config['mscbot']['url'] + "/api/all")
    fcp_info = r.json()
    for issue in issues:
        if msc_labels["proposed-final-comment-period"] in issue["labels"]:
            for fcp in fcp_info:
                if issue["issue"].number == fcp["issue"]["number"]:
                    # Link issue to FCP metadata
                    issue["fcp"] = fcp

    return issues

def main():
    global client
    global config
    global github
    global repo
    global msc_labels

    # Retrieve login information from config file
    with open("config.toml", "r") as f:
        try:
            config = toml.loads(f.read())
        except Exception as e:
            print("Error reading config file:", e)

    # Schedule daily summary messages
    schedule.every().day.at(config["bot"]["daily_summary_time"]).do(send_summary)

    # Login to Github
    github = Github(config["github"]["token"])
    repo = github.get_repo(config["github"]["repo"])
    print("Connected to Github")

    # Get MSC-related label objects from specified Github repository
    labels = (["proposal-in-review",
                  "proposed-final-comment-period",
                  "final-comment-period",
                  "finished-final-comment-period",
                  "spec-pr-missing",
                  "spec-pr-in-review",
                  "merged"])
                
    msc_labels = {label.name: label for label in repo.get_labels() if label.name in labels}

    # Login to Matrix and listen for messages
    homeserver = "https://" + config["matrix"]["user_id"].split(":")[-1]
    client = MatrixClient(homeserver, user_id=config["matrix"]["user_id"], token=config["matrix"]["token"])
    print("Connected to Matrix")
    client.add_invite_listener(invite_received)
    client.add_listener(event_received, event_type="m.room.message")
    client.listen_forever()

    print("Now churning") # Do we need to move a sync request into this loop?
    while True:
        schedule.run_pending()
        time.sleep(60) # Wait one minute between checking time

if __name__ == "__main__":
    main()