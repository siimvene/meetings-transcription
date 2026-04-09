"""
Fallback preprocessing method to meet the bare minimum of tagged translation constraints.
All tags are removed and appended to the translation.
"""
import re
import html
from typing import List, Tuple

from nmt_worker.schemas import InputType

tag_patterns = {
    InputType.SDL: r'<[0-9]+ id=[0-9]+/?>|</[0-9]+>',
    InputType.MEMOQ: r'<[^>]*>'
}

bpt = r'<[^/>]*>'
ept = r'</[^>]*>'

# Other symbols do not need replacing
html_entities = {'<': '&lt;',
                 '>': '&gt;',
                 '&': '&amp;'}


def _classify_tag(item: str) -> str:
    """Classify a tag as 'bpt', 'ept', or 'ph'."""
    if re.match(bpt, item):
        return 'bpt'
    if re.match(ept, item):
        return 'ept'
    return 'ph'


def _extract_tags(sentence: str, pattern: str) -> tuple[str, List[Tuple[str, int, str]]]:
    """Extract tags from a single sentence and return (clean_sentence, tags)."""
    sentence = sentence.strip()
    sentence_tags = []

    tokens = list(filter(None, re.split(rf' |{pattern}', sentence)))
    tokens_w_tags = list(filter(None, re.split(rf' |({pattern})', sentence)))

    clean_sentence = ' '.join(tokens).strip()

    for idx, item in enumerate(tokens_w_tags):
        adjusted_idx = idx - len(sentence_tags)
        if adjusted_idx >= len(tokens) or item != tokens[adjusted_idx]:
            tag_idx = -1 if adjusted_idx >= len(tokens) else adjusted_idx
            sentence_tags.append((item, tag_idx, _classify_tag(item)))

    return clean_sentence, sentence_tags


def preprocess_tags(sentences: List[str], input_type: InputType) -> (List[str], List[List[Tuple[str, int, str]]]):
    if input_type in tag_patterns:
        pattern = tag_patterns[input_type]
        results = [_extract_tags(sentence, pattern) for sentence in sentences]
        clean_sentences = [r[0] for r in results]
        tags = [r[1] for r in results]
    else:
        clean_sentences = sentences
        tags = [[] for _ in sentences]

    clean_sentences = [html.unescape(sentence) for sentence in clean_sentences]

    return clean_sentences, tags


def _retag_sentence(translation: str, sentence_tags: List[Tuple[str, int, str]]) -> str:
    """Re-insert tags into a translated sentence."""
    retagged_sentence = []
    tags = list(sentence_tags)
    tokens = translation.split(' ')

    for idx, token in enumerate(tokens):
        whitespace_added = False
        while tags and tags[0][1] == idx:
            if not whitespace_added and tags[0][2] == 'bpt':
                retagged_sentence.append(' ')
                whitespace_added = True
            retagged_sentence.append(tags.pop(0)[0])
        if not whitespace_added:
            retagged_sentence.append(' ')
        retagged_sentence.append(token)

    remaining = ''.join(tag for tag, _, _ in tags)
    return (''.join(retagged_sentence) + remaining).strip()


def postprocess_tags(translations: List[str], tags: List[List[Tuple[str, int, str]]], input_type: InputType):
    translations = [sentence.replace("<unk>", "") for sentence in translations]

    if input_type in tag_patterns:
        for symbol, entity in html_entities.items():
            translations = [sentence.replace(symbol, entity) for sentence in translations]

    return [_retag_sentence(translation, sentence_tags) for translation, sentence_tags in zip(translations, tags)]
