# Copyright 2017 Mycroft AI, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import re
from xml.etree import ElementTree

import ddg3 as ddg
import requests

from mycroft import AdaptIntent, intent_handler
from mycroft.skills.common_query_skill import CommonQuerySkill, CQSMatchLevel
from mycroft.skills.skill_data import read_vocab_file


def split_sentences(text):
    """
    Turns a string of multiple sentences into a list of separate ones
    handling the edge case of names with initials
    As a side effect, .?! at the end of a sentence are removed
    """
    text = re.sub(r' ([^ .])\.', r' \1~.~', text)
    text = text.replace('Inc.', 'Inc~.~')
    for c in '!?':
        text = text.replace(c + ' ', '. ')
    sents = text.split('. ')
    sents = [i.replace('~.~', '.') for i in sents]
    if sents[-1][-1] in '.!?':
        sents[-1] = sents[-1][:-1]
    return sents


class DuckduckgoSkill(CommonQuerySkill):

    def __init__(self):
        super(DuckduckgoSkill, self).__init__()
        self.is_verb = ' is '
        self.in_word = 'in '
        # get ddg specific vocab for intent match
        fname = self.find_resource("DuckDuckGo.voc", res_dirname="locale")
        temp = read_vocab_file(fname)
        vocab = []
        for item in temp:
            vocab.append(" ".join(item))
        self.sorted_vocab = sorted(vocab, key=lambda x: (-len(x), x))

        self.translated_question_words = self.translate_list("question_words")
        self.translated_question_verbs = self.translate_list("question_verbs")
        self.translated_articles = self.translate_list("articles")
        self.translated_start_words = self.translate_list("start_words")

    def format_related(self, abstract: str, query: str) -> str:
        """Extract answer from a related topic abstract.

        When a disambiguation result is returned. The options are called
        'RelatedTopics'. Each of these has an abstract but they require
        cleaning before use.

        Args:
            abstract: text abstract from a Related Topic.
            query: original search term.
        Returns:
            Speakable response about the query.
        """
        self.log.debug('Original abstract: ' + abstract)
        ans = abstract

        if ans[-2:] == '..':
            while ans[-1] == '.':
                ans = ans[:-1]

            phrases = ans.split(', ')
            first = ', '.join(phrases[:-1])
            last = phrases[-1]
            if last.split()[0] in self.translated_start_words:
                ans = first
            last_word = ans.split(' ')[-1]
            while last_word in self.translated_start_words or last_word[-3:] == 'ing':
                ans = ans.replace(' ' + last_word, '')
                last_word = ans.split(' ')[-1]

        category = None
        match = re.search(r'\(([a-z ]+)\)', ans)
        if match:
            start, end = match.span(1)
            if start <= len(query) * 2:
                category = match.group(1)
                ans = ans.replace('(' + category + ')', '()')

        words = ans.split()
        for article in self.translated_articles:
            article = article.title()
            if article in words:
                index = words.index(article)
                if index <= 2 * len(query.split()):
                    name, desc = words[:index], words[index:]
                    desc[0] = desc[0].lower()
                    ans = ' '.join(name) + self.is_verb + ' '.join(desc)
                    break

        if category:
            ans = ans.replace('()', self.in_word + category)

        if ans[-1] not in '.?!':
            ans += '.'
        return ans

    def query_ddg(self, query: str) -> str:
        """Query DuckDuckGo about the search term.

        Args:
            query: search term to use.
        Returns:
            Short text summary about the query.
        """
        self.log.debug("Query: %s" % (str(query),))
        if len(query) == 0:
            return None

        # note: '1+1' throws an exception
        try:
            response = ddg.query(query)
        except Exception as e:
            self.log.warning("DDG exception %s" % (e,))
            return None

        self.log.info(response.image)
        self.log.debug("Type: %s" % (response.type,))

        # if disambiguation, save old result for fallback
        # but try to get the real abstract
        if response.type == 'disambiguation':
            if response.related:
                detailed_url = response.related[0].url + "?o=x"
                self.log.debug("DDG: disambiguating %s" % (detailed_url,))
                request = requests.get(detailed_url)
                detailed_response = request.content
                if detailed_response:
                    xml = ElementTree.fromstring(detailed_response)
                    response = ddg.Results(xml)

        if (response.answer is not None and response.answer.text and
                "HASH" not in response.answer.text):
            return(query + self.is_verb + response.answer.text + '.')
        elif len(response.abstract.text) > 0:
            sents = split_sentences(response.abstract.text)
            # return sents[0]  # what it is
            # return sents     # what it should be
            return ". ".join(sents)   # what works for now
        elif len(response.related) > 0 and len(response.related[0].text) > 0:
            related = split_sentences(response.related[0].text)[0]
            return(self.format_related(related, query))
        else:
            return None

    def extract_topic(self, query: str) -> str:
        """Extract the topic of a query.

        Args:
            query: user utterance eg 'what is the earth'
        Returns:
            topic of question eg 'earth' or original query
        """
        for noun in self.translated_question_words:
            for verb in self.translated_question_verbs:
                for article in [i + ' ' for i in self.translated_articles] + ['']:
                    test = noun + verb + ' ' + article
                    if query[:len(test)] == test:
                        return query[len(test):]
        return query

    def CQS_match_query_phrase(self, query: str):
        """Respond to Common Query framework with best possible answer.

        Args:
            query: question to answer

        Returns:
            Tuple(
                question being answered,
                CQS Match Level confidence,
                answer to question,
                callback dict available to CQS_action method
            )
        """
        answer = None
        for noun in self.translated_question_words:
            for verb in self.translated_question_verbs:
                for article in [i + ' ' for i in self.translated_articles] + ['']:
                    test = noun + verb + ' ' + article
                    if query[:len(test)] == test:
                        answer = self.query_ddg(query[len(test):])
                        break
        if answer:
            return (query, CQSMatchLevel.CATEGORY, answer)
        else:
            self.log.debug("DDG has no answer")
            return None

    @intent_handler(AdaptIntent("AskDucky").require("DuckDuckGo"))
    def handle_ask_ducky(self, message):
        """Intent handler to request information specifically from DDG."""
        utt = message.data['utterance']

        if utt is None:
            return

        for voc in self.sorted_vocab:
            utt = utt.replace(voc, "")

        utt = utt.strip()
        utt = self.extract_topic(utt)
        utt = utt.replace("an ", "")   # ugh!
        utt = utt.replace("a ", "")   # ugh!
        utt = utt.replace("the ", "")   # ugh!

        if utt is not None:
            response = self.query_ddg(utt)
            if response is not None:
                self.speak_dialog("ddg.specific.response")
                self.speak(response)


def create_skill():
    return DuckduckgoSkill()
