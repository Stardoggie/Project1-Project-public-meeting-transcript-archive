from meeting_transcript.aws.aws import get_comprehend_client
from meeting_transcript.analysis.store import split_transcripton_chunk,add_entities_and_phrases
from collections import Counter


def comprehend_entities_and_phrases(text:str,meeting_id:int,min_score=0.99)->None:
    """
        gets entities and key phrases from comprehend using chunks of transcript
    """

    entity_counts = {}
    key_phrase = set()
    chunks = split_transcripton_chunk(text)
    allowed_types = {"PERSON", "ORGANIZATION", "LOCATION"}
    ignored_phrases = {"today","tomorrow","the meeting","the council","this time","a couple","a number","a couple"} #gets rid of generic stuff that would show up in every meeting
    for chunk in chunks:
        entity_response = get_comprehend_client().detect_entities(Text=chunk,LanguageCode="en")
        """
            extracts the entity from entity response an puts it in a small dictionary
            -also counts how many of them are the same thing
        """
        for entity in entity_response["Entities"]:
            if entity["Score"] >= min_score and entity["Type"] in allowed_types:   
                text = entity["Text"].lower()
                if text not in entity_counts:
                    entity_counts[text] = 0
                entity_counts[text] += 1
        phrase_response = get_comprehend_client().detect_key_phrases(Text=chunk,LanguageCode="en")
        """
            does the same as the one above but for key phrases without any counting
        """
        for phrase in phrase_response["KeyPhrases"]:
            #text added to this one for more filtering out of actual garbage phrases like uh and tomorrow 
            text = phrase["Text"].strip()
            if phrase["Score"] < min_score:
                continue
            if len(text.split()) < 2:
                continue

            if len(text) > 150:
                continue

            if not any(char.isalpha() for char in text):
                continue

            if text.lower() in ignored_phrases:
                continue
            
            if len(text) < 5:
                continue
            key_phrase.add(phrase["Text"].lower())
    add_entities_and_phrases(key_phrase,entity_counts,meeting_id)



    