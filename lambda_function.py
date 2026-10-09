import json, os, time, urllib.request, urllib.parse
import boto3

ec2 = boto3.client("ec2")
TOKEN = os.environ["TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])
LIMIT = int(os.environ.get("LIMIT_MINUTES", "120")) * 60
GRACE = int(os.environ.get("GRACE_MINUTES", "15")) * 60
API = f"https://api.telegram.org/bot{TOKEN}"


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=10) as r:
        return json.load(r)


def say(text):
    tg("sendMessage", chat_id=CHAT_ID, text=text)


def running_instances():
    found = []
    pages = ec2.get_paginator("describe_instances").paginate(Filters=[
        {"Name": "instance-state-name", "Values": ["running"]},
        {"Name": "tag:KillSwitch", "Values": ["on"]},
    ])
    for page in pages:
        for res in page["Reservations"]:
            found.extend(res["Instances"])
    return found


def tag_dict(inst):
    return {t["Key"]: t["Value"] for t in inst.get("Tags", [])}


def keep_alive_requested():
    updates = tg("getUpdates")["result"]
    if not updates:
        return False
    # mark all as read so they aren't processed twice
    tg("getUpdates", offset=updates[-1]["update_id"] + 1)
    for u in updates:
        msg = u.get("message", {})
        sender = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "").strip().upper()
        if sender == CHAT_ID and text == "KEEP ALIVE":
            return True
    return False


def lambda_handler(event, context):
    now = int(time.time())
    instances = running_instances()
    keep = keep_alive_requested()

    for inst in instances:
        iid = inst["InstanceId"]
        t = tag_dict(inst)

        if keep:
            ec2.create_tags(Resources=[iid],
                            Tags=[{"Key": "ks-start", "Value": str(now)}])
            ec2.delete_tags(Resources=[iid], Tags=[{"Key": "ks-warned"}])
            say(f"✅ {iid} kept alive. Clock reset: {LIMIT // 60} more minutes.")
            continue

        launch = int(inst["LaunchTime"].timestamp())
        start = max(int(t.get("ks-start", 0)), launch)
        uptime = now - start

        if "ks-warned" in t:
            if now - int(t["ks-warned"]) >= GRACE:
                ec2.stop_instances(InstanceIds=[iid])
                ec2.delete_tags(Resources=[iid], Tags=[{"Key": "ks-warned"}])
                say(f"🛑 No reply. Stopped {iid} to save your money.")
        elif uptime >= LIMIT:
            ec2.create_tags(Resources=[iid],
                            Tags=[{"Key": "ks-warned", "Value": str(now)}])
            say(f"⚠️ {iid} has run {uptime // 60} min. "
                f"Stopping in {GRACE // 60} min. Reply KEEP ALIVE to cancel.")

    return {"checked": len(instances), "keep_alive": keep}
