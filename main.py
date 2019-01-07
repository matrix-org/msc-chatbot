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
import logging

client = None
config = None
github = None
repo = None
msc_labels = None
logger = None

# Custom variadic functions for logging purposes
def log_info(*args):
    global logger
    logger.info(' '.join([str(arg) for arg in args]))

def log_warn(*args):
    global logger
    logger.warn(' '.join([str(arg) for arg in args]))

def log_fatal(*args):
    global logger
    logger.fatal(' '.join([str(arg) for arg in args]))

def invite_received(room_id, state):
    """Matrix room invite received. Join the room"""
    global client
    time.sleep(3) # Workaround for Synapse#2807
    try:
        log_info("Joining room:", room_id)
        client.join_room(room_id)
    except Exception:
        log_warn("Unable to join room:", room_id)
        log_warn("Trying again...")
        time.sleep(5)
        invite_received(room_id, state)

def event_received(event):
    """Matrix event received. Act if it was directed at us"""
    global client
    if event["content"]["msgtype"] != "m.text":
        return

    body = event["content"]["body"].strip()
    room = client.get_rooms()[event["room_id"]]
    username = config["bot"]["command"]
    if body.startswith(username + ":"):
        command = body[8:].strip()
        log_info("Received command:", command)
        if (not command.startswith("show new") and
           not command.startswith("show pending") and
           not command.startswith("show fcp") and
           not command.startswith("show all") and
           not command.startswith("help")):
            room.send_html("Unknown command.", msgtype="m.notice")
            return

        # Retrieve MSC information from Github labels
        mscs = get_mscs()

        try:
            if command.startswith("show new"):
                response = reply_new_mscs(mscs)
            elif command.startswith("show pending"):
                response = reply_pending_mscs(mscs)
            elif command.startswith("show fcp"):
                response = reply_fcp_mscs(mscs)
            elif command.startswith("show all"):
                response = reply_all_mscs(mscs)
            elif command.startswith("help"):
                response = ("Available commands:\n\n" +
                            "Show MSCs that are still being finalized:\n\n"
                            "<pre><code>" + 
                            "show new" +
                            "</pre></code>\n\n" + 
                            "Show MSCs which are pending a FCP. These need review from team members:\n\n" +
                            "<pre><code>" + 
                            "show pending" +
                            "</pre></code>\n\n" + 
                            "Show MSCs that are currently in FCP:\n\n"
                            "<pre><code>" + 
                            "show fcp" +
                            "</pre></code>\n\n" + 
                            "Combined response of all of the above:\n\n" +
                            "<pre><code>" + 
                            "show all" +
                            "</pre></code>\n\n" +
                            "Summaries are shown every day at %s UTC" % config["bot"]["daily_summary_time"])

            # Send the response
            log_info("Sending command response")
            room.send_html(markdown(response), body=response, msgtype="m.notice")
        except:
            log_warn("Unable to post to room:")

def send_summary():
    """Sends a daily summary of MSCs to every room the bot is in"""
    # TODO: Ability to turn this on or off per room
    global client
    global config

    # Get MSC metadata from Github labels
    mscs = get_mscs()

    info = reply_all_mscs(mscs)

    if "msc_goal" in config["bot"]:
        # Count finished mscs
        completed_mscs = 0
        for msc in mscs:
            if (("proposal-in-review" not in msc["labels"] and
               "proposed-final-comment-period" not in msc["labels"] and
               "final-comment-period" not in msc["labels"]) or
               "finished-final-comment-period" in msc["labels"]):
               completed_mscs += 1
        info += "\n\nr0 Progress: %d/%d" % (completed_mscs, config["bot"]["msc_goal"])

    for room in list(client.get_rooms().values()):
        try:
            room.send_html(markdown(info), body=info, msgtype="m.notice")
        except:
            log_warn("Unable to send daily summary")

def reply_new_mscs(mscs):
    """Returns a formatted reply with MSCs that are proposed but not yet pending an FCP"""
    new = []
    for msc_dict in mscs:
        msc = msc_dict["issue"]
        labels = msc_dict["labels"]
        if msc_labels["proposal-in-review"] in labels:
            new.append("[[MSC%d](%s)] - %s" % (msc.number, msc.url, msc.title))

    response = "\n\n**New**\n\n"
    if len(new) > 0:
        response += '\n\n'.join(new)
    else:
        response += "\n\nNo new MSCs."

    return response

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

    response = "\n\n**Pending Final Comment Period**\n\n"
    if len(pending) > 0:
        response += '\n\n'.join(pending)
    else:
        response += 'No MSCs pending FCP.'

    return response

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
                    break

            remaining_days = config["msc"]["fcp_length"] - (datetime.today() - time).days
            line = "[[MSC%d](%s)] - %s" % (msc.number, msc.url, msc.title)
            line += " - Ends in **%d %s**" % (remaining_days, "day" if remaining_days == 1 else "days")
            fcps.append(line)

    response = "\n\n**In Final Comment Period**\n\n"
    if len(fcps) > 0:
        response += '\n\n'.join(fcps)
    else:
        response += "No MSCs in FCP."

    return response

def reply_all_mscs(mscs):
    """Returns a formatted reply with MSCs that are proposed, pending or in FCP. Used as daily message."""
    global client

    # Sort MSCs by ID
    mscs = sorted(mscs, key=lambda msc: msc["issue"].number)

    # Display active MSCs by status: proposed, fcp pending, and fcp
    response = "# Today's MSC Status\n\n"
    response += reply_new_mscs(mscs)
    response += reply_pending_mscs(mscs)
    response += reply_fcp_mscs(mscs)
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
            # Skip non-priority issues if priority is defined
            if "priority_mscs" in config["bot"] and issue.number not in config["bot"]["priority_mscs"]:
                continue
            issues.append(issue)

    # Create a list relating an issue to its possible FCP information (FCP info added later)
    issues = [({"issue": issue,
                "labels": list(issue.get_labels()),
                "fcp": None}) for issue in issues]

    # Link issues to metadata from MSCBot
    r = requests.get(config['mscbot']['url'] + "/api/all")
    fcp_info = r.json()
    for issue in issues:
        # Link MSC to FCP metadata if current in ongoing FCP
        if msc_labels["proposed-final-comment-period"] in issue["labels"]:
            for fcp in fcp_info:
                if issue["issue"].number == fcp["issue"]["number"]:
                    issue["fcp"] = fcp

    return issues

def main():
    global client
    global config
    global github
    global repo
    global msc_labels
    global logger

    # Retrieve login information from config file
    with open("config.toml", "r") as f:
        try:
            config = toml.loads(f.read())
        except Exception as e:
            log_fatal("Error reading config file:", e)
            return

    # Configure logging
    # Determine whether we are using a logfile or not
    logging_format = "[%(levelname)s] %(asctime)s: %(message)s"
    if "logfile" in config["logging"]:
        logging.basicConfig(level=logging.INFO if config["logging"]["level"] != "DEBUG" else logging.DEBUG,
                            format=logging_format,
                            filename=config["logging"]["logfile"])
    else:
        logging.basicConfig(level=logging.INFO if config["logging"]["level"] != "DEBUG" else logging.DEBUG,
                            format=logging_format)
    logger = logging.getLogger()

    # Schedule daily summary messages
    schedule.every().day.at(config["bot"]["daily_summary_time"]).do(send_summary)

    # Login to Github
    github = Github(config["github"]["token"])
    repo = github.get_repo(config["github"]["repo"])
    log_info("Connected to Github")

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
    log_info("Connected to Matrix")
    client.add_invite_listener(invite_received)
    client.add_listener(event_received, event_type="m.room.message")

    # Sync continuously and check time for daily summary sending
    while True:
        try:
            client.listen_for_events()
        except:
            log_warn("Unable to contact /sync")
        schedule.run_pending()
        time.sleep(5) # Wait a few seconds between syncs

if __name__ == "__main__":
    main()