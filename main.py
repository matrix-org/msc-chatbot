#!/usr/bin/env python3
"""
A bot for matrix users to interact with and to be reminded about various events involving MSC proposals.
"""

from matrix_client.client import MatrixClient
from datetime import datetime, timedelta
from markdown import markdown
from github import Github
import traceback
import parsedatetime
import schedule
import time
import toml
import json
import requests
import logging
import sys
import os

client = None
config = None
github = None
repo = None
msc_labels = None
logger = None
# Room ID to room-settings dictionary mapping
room_specific_data = {}

# Available bot commands and their variants.
# Certain commands can accept parameters which should immediately follow the
# command text
known_commands = {
    # General bot commands
    "SHOW_NEW": ["show new"],
    "SHOW_PENDING": ["show pending"],
    "SHOW_FCP": ["show fcp", "show in fcp"],
    "SHOW_ALL": ["show all", "show active"],
    "SHOW_SUMMARY": ["show summary", "summarize", "summary", "summarise"],
    "HELP": ["help", "show help"],

    # Room-specific commands
    "ROOM_SUMMARY_CONTENT": ["set summary content"],
    "ROOM_SUMMARY_ENABLE": ["set enable summary", "set summary enable", "set summary enabled"],
    "ROOM_SUMMARY_DISABLE": ["set disable summary", "set summary disable", "set summary disabled"],
    "ROOM_SUMMARY_TIME": ["set time summary", "set summary time", "set summary time to"],
    "ROOM_SUMMARY_TIME_INFO": ["show summary time", "summary time", "get summary time"],
    "ROOM_SHOW_PRIORITY": ["show priority", "priority", "priorities"],
    "ROOM_PRIORITY_MSCS": ["set priority mscs", "set priority"],
}

# Custom variadic functions for logging purposes
def log_info(*args, trace=False):
    global logger
    err = ' '.join([str(arg) for arg in args])
    if trace:
        err += '\n' + traceback.format_exc()
    logger.info(err)

def log_warn(*args, trace=True):
    global logger
    err = ' '.join([str(arg) for arg in args])
    if trace:
        err += '\n' + traceback.format_exc()
    logger.warn(err)

def log_fatal(*args, trace=True):
    global logger
    err = ' '.join([str(arg) for arg in args])
    if trace:
        err += '\n' + traceback.format_exc()
    logger.fatal(err)

def get_room_setting(room_id, setting_key):
    """Retreives a room setting if it exists, otherwise returns None"""
    global room_specific_data

    if room_id in room_specific_data and setting_key in room_specific_data[room_id]:
        return room_specific_data[room_id][setting_key]
    return None

def update_room_setting(room_id, setting_dict):
    """
    Update a room-specific setting and save to disk. Params are room ID
    string and a dictionary with custom key/value data.
    """
    global config
    global room_specific_data

    # Update or insert settings dict under room_id key
    if room_id not in room_specific_data:
        room_specific_data[room_id] = setting_dict
    else:
        room_specific_data[room_id].update(setting_dict)

    # Backup old room data if available
    data_filepath = config["bot"]["data_filepath"]
    if os.path.exists(data_filepath):
        os.rename(data_filepath, data_filepath + ".bak")

    # Save updated data to disk
    try:
        with open(data_filepath, 'w') as f:
            json.dump(room_specific_data, f)
    except:
        log_warn("Unable to save room data to disk")

def delete_room_setting(room_id, setting_key):
    """Removes a setting from a room"""
    global config
    global room_specific_data

    try:
        room_specific_data[room_id].pop(setting_key, None)
    except:
        log_warn("Tried to delete room key '%s' that did not exist on room '%s'." % (setting_key, room_id))
        return

    # Backup old room data if available
    data_filepath = config["bot"]["data_filepath"]
    if os.path.exists(data_filepath):
        os.rename(data_filepath, data_filepath + ".bak")

    # Save updated data to disk
    try:
        with open(data_filepath, 'w') as f:
            json.dump(room_specific_data, f)
    except:
        log_warn("Unable to save room data to disk")

def invite_received(room_id, state):
    """Matrix room invite received. Join the room"""
    global client
    time.sleep(3) # Workaround for Synapse#2807
    try:
        log_info("Joining room:", room_id)
        client.join_room(room_id)
    except:
        log_warn("Unable to join room:", room_id)
        log_warn("Trying again...")
        time.sleep(5)
        invite_received(room_id, state)

def match_command(command):
    """Returns a command ID on match, or None if no match"""
    for key, command_list in known_commands.items():
        for com in command_list:
            if command.startswith(com):
                return key
    return None

def process_cmd_text(room_id, command, handler, command_id):
    """
    Pre-process command text to only retrieve command arguments and pass them
    to handler function
    """

    # Figure out which varation of the command was used
    longest_match_length = 0
    for variation in known_commands[command_id]:
        if command.startswith(variation):
            longest_match_length = len(variation)

    # Get just the arguments by removing the longest match command
    arguments = command[longest_match_length:].split()

    # Clean up any spaces
    arguments = [arg.strip() for arg in arguments]

    return handler(room_id, arguments)

def event_received(event):
    """Matrix event received. Act if it was directed at us"""
    global client
    if event["content"]["msgtype"] != "m.text":
        return

    body = event["content"]["body"].strip()
    room = client.get_rooms()[event["room_id"]]
    room_id = room.room_id
    username = config["bot"]["command"]
    if body.startswith(username + ":"):
        command = body[8:].strip()
        log_info("Received command:", command)
        command_id = match_command(command)
        if command_id == None:
            room.send_html("Unknown command.", msgtype="m.notice")
            return

        # Retrieve MSC information from Github labels
        mscs = get_mscs(room_id)

        if command_id == "SHOW_NEW":
            response = reply_new_mscs(mscs)
        elif command_id == "SHOW_PENDING":
            response = reply_pending_mscs(mscs)
        elif command_id == "SHOW_FCP":
            response = reply_fcp_mscs(mscs)
        elif command_id == "SHOW_ALL":
            response = reply_all_mscs(mscs)
        elif command_id == "HELP":
            response = show_help(room_id)
        elif command_id == "ROOM_SUMMARY_CONTENT":
            response = process_cmd_text(room_id, command, room_summary_content, "ROOM_SUMMARY_CONTENT")
        elif command_id == "ROOM_SUMMARY_ENABLE":
            response = process_cmd_text(room_id, command, room_summary_enable, "ROOM_SUMMARY_ENABLE")
        elif command_id == "ROOM_SUMMARY_DISABLE":
            response = process_cmd_text(room_id, command, room_summary_disable, "ROOM_SUMMARY_DISABLE")
        elif command_id == "ROOM_SUMMARY_TIME":
            response = process_cmd_text(room_id, command, room_summary_time, "ROOM_SUMMARY_TIME")
        elif command_id == "ROOM_SUMMARY_TIME_INFO":
            response = process_cmd_text(room_id, command, room_summary_time_info, "ROOM_SUMMARY_TIME_INFO")
        elif command_id == "ROOM_SHOW_PRIORITY":
            response = process_cmd_text(room_id, command, room_show_priority, "ROOM_SHOW_PRIORITY")
        elif command_id == "ROOM_PRIORITY_MSCS":
            response = process_cmd_text(room_id, command, room_priority_mscs, "ROOM_PRIORITY_MSCS")
        elif command_id == "SHOW_SUMMARY":
            send_summary(room_id)
            return # send_summary sends its own message

        try:
            # Send the response
            log_info("Sending command response to %s" % room_id)
            room.send_html(markdown(response), body=response, msgtype="m.notice")
            log_info("Sent to %s" % room_id)
        except:
            log_warn("Unable to post to room")

def show_help(room_id):
    """Return help text"""
    global config

    response = ("""#Available commands:

**MSCs**

Show MSCs that are still being finalized:
<pre><code>show new
</code></pre>

Show MSCs which are pending a FCP. These need review from team members:

<pre><code>show pending
</code></pre>

Show MSCs that are currently in FCP:

<pre><code>show fcp
</code></pre>

Combined response of all of the above:

<pre><code>show all
</code></pre>

Show the summary once for this room, whether it is enabled daily or not:

<pre><code>show summary
</code></pre>

**Per-room Bot Options**

Set priority MSCs. If set, only information about these MSCs will be shown:

<pre><code>set priority 123, 456, 555, 12
</code></pre>

Show set priority MSCs for this room:

<pre><code>show priority
</code></pre>

Clear priority MSCs:

<pre><code>set priority clear
</code></pre>

Enable/disable daily summary:

<pre><code>set summary enable|disable
</code></pre>

Set daily summary time:

<pre><code>set summary time 08:00|8am|8:15pm|etc.
</code></pre>

Show the currently configured daily summary time:

<pre><code>show summary time
</code></pre>

Set the content a daily summary will contain:

<pre><code>set summary content all|pending|fcp|new
</code></pre>

all: All MSCs currently in-flight<br>
pending: MSCs that are currently being voted on for an FCP<br>
fcp: MSCs that are currently in FCP<br>
new: MSCs that are currently in the discussion phase

**Other**

Show this help:

<pre><code>help
</code></pre>

""")

    # Show current room summary status
    default_time = config["bot"]["daily_summary_time"]
    custom_time = get_room_setting(room_id, "summary_time")
    if get_room_setting(room_id, "summary_enabled") == False:
        response += "Summaries are currently disabled for this room."
    else:
        if custom_time:
            response += "Summaries are currently shown every day at %s UTC." % custom_time
        else:
            response += "Summaries are currently shown every day at %s UTC." % default_time

    return response

# Room Specific Commands
def room_priority_mscs(room_id, arguments):
    """Room-specific option to filter output by specific MSC numbers"""
    if len(arguments) == 0:
        return "Unknown MSC numbers. Usage: set priority 123, 456, 555, 12"

    if arguments[0] == "clear":
        priority = get_room_setting(room_id, "priority_mscs")
        delete_room_setting(room_id, "priority_mscs")
        return "Priority MSCs cleared. Was: %s." % priority

    numbers = []
    for num_str in arguments:
        # Remove ,'s if numbers are in a comma-separated list
        num_str = num_str.replace(",", "")

        # Convert MSC number to integer
        try:
            num = int(num_str)
            numbers.append(num)
        except:
            log_warn("Unable to parse %s as an int" % num_str)
            return "Unable to parse %s as an MSC number. Make sure it is a valid integer." % num_str

    update_room_setting(room_id, {"priority_mscs": numbers})
    return "Priority MSCs set: %s" % str(numbers)

def room_show_priority(room_id, arguments):
    """Show the currently-set priority MSCs for a room"""
    priority_mscs = get_room_setting(room_id, "priority_mscs")

    if priority_mscs:
        return "Currently set priority MSCs: %s" % str(priority_mscs)

    return "No priority MSCs set."

def room_summary_content(room_id, arguments):
    """Room-specific option for daily summary contents"""

    allowed = ["all", "pending", "fcp", "new"]

    if len(arguments) == 0 or arguments[0] not in allowed:
        return ("""
Invalid or unknown summary content option.
        
Usage: `set summary content: [all, pending, fcp, new]`""")

    update_room_setting(room_id, {"summary_content": arguments[0]})
    return "Summary content updated successfully to '%s'." % arguments[0]

def room_summary_enable(room_id, arguments):
    """Enable daily summary for this room"""
    update_room_setting(room_id, {"summary_enabled": True})
    return "Daily summary enabled."

def room_summary_disable(room_id, arguments):
    """Disable daily summary for this room"""
    update_room_setting(room_id, {"summary_enabled": False})
    return "Daily summary disabled."

def room_summary_time_info(room_id, arguments):
    """Show current summary time configured for this room"""
    global room_specific_data
    global config

    response = "The currently configured daily summary time for this room is "
    time = get_room_setting(room_id, "summary_time")
    if time:
        response += time
    else:
        response += config["bot"]["daily_summary_time"]
    response += " UTC."

    if get_room_setting(room_id, "summary_enabled") == False:
        response += " However, summaries in this room are currently disabled."

    return response

def room_summary_time(room_id, arguments):
    """Set the daily time for the room summary"""
    if len(arguments) == 0:
        return ("""
Invalid or unknown summary time option.

Usage: `set summary time 07:00` or `set summary time 4pm`""")

    try:
        cal = parsedatetime.Calendar()
        time = cal.parse(arguments[0])[0]

        # Convert hour/minutes to string
        if time.tm_hour < 10:
            hour = "0%d" % time.tm_hour
        else:
            hour = "%d" % time.tm_hour

        if time.tm_min < 10:
            min = "0%d" % time.tm_min
        else:
            min = "%d" % time.tm_min

        # Convert to 24hr time to hand off to schedule lib
        time_24hr = "%s:%s" % (hour, min)

        # Update time in room settings
        update_room_setting(room_id, {"summary_time": time_24hr})

        # Cancel old time scheduler
        schedule.clear(room_id)

        # Add scheduler for new time
        schedule.every().day.at(time_24hr).do(send_summary, room_id).tag(room_id)

        return "Summary time now set to %s." % time_24hr
    except:
        log_warn("Unable to parse time: '%s" % arguments[0])
        return "Unknown time parameter '%s'." % arguments[0]

def set_up_default_summaries():
    """Sets up a scheduler for a daily summary for all rooms that do not have a schedule set"""
    global client
    global room_specific_data

    for room_id in room_specific_data.keys():
        if get_room_setting(room_id, "summary_time"):
            continue
        if get_room_setting(room_id, "summary_enabled") == False:
            continue

        # Schedule a summary
        schedule.every().day.at(config["bot"]["daily_summary_time"]).do(send_summary, room_id).tag(room_id)

def send_summary(room_id):
    """
    Sends a daily summary of MSCs to the specified room.
    Returns False if summaries are not enabled for this room, otherwise True
    """
    global config

    # Get MSC metadata from Github labels
    mscs = get_mscs(room_id)

    # See which summary mode this room wants
    mode = get_room_setting(room_id, "summary_content")
    if mode == "new":
        info = reply_new_mscs(mscs)
    elif mode == "pending":
        info = reply_pending_mscs(mscs)
    elif mode == "fcp":
        info = reply_fcp_mscs(mscs)
    elif mode == "all":
        info = reply_all_mscs(mscs)
    else:
        log_warn("Unknown summary mode for room %s: %s" % (room_id, mode), trace=False)

    # Print MSC goal progress if a goal is set
    # TODO: Place in title/Erik's weird Riot header thingy
    priority_mscs = get_room_setting(room_id, "priority_mscs")
    if priority_mscs:
        goal = len(priority_mscs)
        completed_mscs = 0

        for msc in mscs:
            # Skip non-priority MSCs
            if msc["issue"].number not in priority_mscs:
                continue

            # Check if this MSC has passed final comment period
            if (("proposal-in-review" not in msc["labels"] and
               "proposed-final-comment-period" not in msc["labels"] and
               "final-comment-period" not in msc["labels"]) or
               "finished-final-comment-period" in msc["labels"]):
               completed_mscs += 1

        info += "\n\nPriority MSC progress: %d/%d" % (completed_mscs, goal)

    # Send summary
    try:
        room = client.get_rooms()[room_id]
        room.send_html(markdown(info), body=info, msgtype="m.notice")
    except:
        log_warn("Unable to send daily summary to %s", room_id)

    return True

def reply_new_mscs(mscs):
    """Returns a formatted reply with MSCs that are proposed but not yet pending an FCP"""
    new = []
    for msc_dict in mscs:
        msc = msc_dict["issue"]
        labels = msc_dict["labels"]
        if msc_labels["proposal-in-review"] in labels:
            new.append("[[MSC%d](%s)] - %s" % (msc.number, msc.html_url, msc.title))

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
            line = "[[MSC%d](%s)] - %s - *%s*" % (msc.number, msc.html_url, msc.title, fcp["fcp"]["disposition"])
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
                if comment.user.id == 40832866:
                    time = comment.created_at - timedelta(days=1)
                    break

            remaining_days = config["msc"]["fcp_length"] - (datetime.today() - time).days
            line = "[[MSC%d](%s)] - %s" % (msc.number, msc.html_url, msc.title)
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

def get_mscs(room_id=None):
    """
    Get up to date MSC metadata from Github.
    If room_id is set, and that room has priority MSCs set, only metadata
    about those MSCs will be returned
    """
    global github
    global repo
    global msc_labels

    # Download issues/pulls from github with active MSC labels
    issues = []
    for issue in repo.get_issues(labels=list([msc_labels["proposal"]])):
        # Check if a room ID with priority MSCs was provided
        # Filter out any mscs that aren't a priority for this room
        if room_id:
            priority_mscs = get_room_setting(room_id, "priority_mscs")
            if priority_mscs and issue.number not in priority_mscs:
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
    global room_specific_data

    # Retrieve login information from config file
    with open("config.toml", "r") as f:
        try:
            config = toml.loads(f.read())
        except:
            log_fatal("Error reading config file:")
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

    # Retrieve room-specific data if config file exists
    if "data_filepath" in config["bot"]:
        data_filepath = config["bot"]["data_filepath"]
        if os.path.exists(config["bot"]["data_filepath"]):
            with open(data_filepath, 'r') as f:
                room_specific_data = json.loads(f.read())

    # Schedule daily summary messages per-room
    for room_id in room_specific_data.keys():
        # Check if summaries are enabled in this room
        if get_room_setting(room_id, "summary_enabled") == False:
            continue

        # Check if this room has a custom summary time
        if get_room_setting(room_id, "summary_time"):
            # Set a scheduler for that time
            # Tag with the room ID so we can easily cancel later if necessary
            schedule.every().day.at(config["bot"]["daily_summary_time"]).do(send_summary, room_id).tag(room_id)

    # Schedule daily summary messages to rooms that do not have a custom time
    set_up_default_summaries()

    # Login to Github
    github = Github(config["github"]["token"])
    repo = github.get_repo(config["github"]["repo"])
    log_info("Connected to Github")

    # Get MSC-related label objects from specified Github repository
    labels = config["github"]["labels"]
    msc_labels = {label.name: label for label in repo.get_labels() if label.name in labels}

    # Login to Matrix and listen for messages
    homeserver = "https://" + config["matrix"]["user_id"].split(":")[-1]
    client = MatrixClient(homeserver, user_id=config["matrix"]["user_id"], token=config["matrix"]["token"])
    client.add_invite_listener(invite_received)
    client.add_listener(event_received, event_type="m.room.message")
    log_info("Connected to Matrix")

    # Sync continuously and check time for daily summary sending
    while True:
        try:
            client.listen_for_events()
        except:
            log_warn("Unable to contact /sync")
        schedule.run_pending()
        time.sleep(config["matrix"]["sync_interval"]) # Wait a few seconds between syncs

if __name__ == "__main__":
    main()