"""The two kinds of accounts besides superusers: clerks (the admin) and viewers (the chatbot page only)."""
CLERKS = "Clerks"
VIEWERS = "Viewers"
# What clerks may do: manage bodies and meetings, read what Comprehend found, and look after viewer accounts
CLERK_PERMISSIONS = [
    "records.add_governingbody", "records.change_governingbody", "records.delete_governingbody",
    "records.view_governingbody",
    "records.add_meeting", "records.change_meeting", "records.delete_meeting", "records.view_meeting",
    "records.view_entity", "records.view_keyphrase",
    "auth.add_user", "auth.change_user", "auth.view_user"
]
