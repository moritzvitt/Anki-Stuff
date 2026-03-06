import requests
import json
import re

def invoke(action, **params):
    return requests.post(
        "http://localhost:8765",
        json={
            "action": action,
            "version": 6,
            "params": params
        }
    ).json()

# example: get deck names
print(invoke("deckNames"))


#find all the Notes, that I am already learning in the "Japanese Media" deck, so that I can unsuspend their RTK card.
# take care of the fact that there might be more than one note type in this deck!

note_ids = invoke(
    "findNotes",
    query='deck:"Japanese Media::Youtube::Konnichiwa My Dude Japanese Podcast::ジブリ愛が止まらない | 日本語ポッドキャスト EP299"'
)

#get information about those notes, to find the RTK card ids
notes_info = invoke(
    "notesInfo",
    notes=note_ids['result']
)
#DONT FORGET that in this deck are 2 different note types!!!

#field names
print("Fields in each note:")
for note in notes_info['result'][:1]:  # Print only the first note's fields for inspection
    print(list(note['fields'].keys()))




