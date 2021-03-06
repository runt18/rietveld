#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2011 Google Inc.
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

import unittest

import setup
setup.process_args()


from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.appengine.api.users import User

from utils import TestCase

from codereview import models, views


class TestIncomingMail(TestCase):

  def setUp(self):
    super(TestIncomingMail, self).setUp()
    self.login('foo@example.com')
    self.issue = models.Issue(subject='test')
    self.issue.put()
    self.issue2 = models.Issue(subject='test2')
    self.issue2.put()
    self.logout()

  def test_incoming_mail(self):
    msg = Message()
    msg['To'] = 'reply@example.com'
    msg['From'] = 'sender@example.com'
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg.set_payload('body')
    response = self.client.post('/_ah/mail/reply@example.com',
                                msg.as_string(), content_type='text/plain')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(models.Message.query(ancestor=self.issue.key).count(), 1)
    self.assertEqual(models.Message.query(ancestor=self.issue2.key).count(), 0)
    msg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(msg.text, 'body')
    self.assertEqual(msg.subject,
                     'subject (issue{0!s})'.format(self.issue.key.id()))
    self.assertEqual(msg.sender, 'sender@example.com')
    self.assertEqual(msg.recipients, ['reply@example.com'])
    self.assert_(msg.date is not None)
    self.assertEqual(msg.draft, False)

  def test_incoming_mail_invalid_subject(self):
    msg = Message()
    msg['To'] = 'reply@example.com'
    msg['From'] = 'sender@example.com'
    msg['Subject'] = 'invalid'
    msg.set_payload('body')
    response = self.client.post('/_ah/mail/reply@example.com',
                                msg, content_type='text/plain')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(models.Message.query(ancestor=self.issue.key).count(), 0)

  def test_unknown_issue(self):
    msg = Message()
    msg['From'] = 'sender@example.com'
    msg['Subject'] = 'subject (issue99999)'
    msg.set_payload('body')
    self.assertRaises(views.InvalidIncomingEmailError,
                      views._process_incoming_mail, msg.as_string(),
                      'reply@example.com')

  def test_empty_message(self):
    msg = Message()
    msg['From'] = 'sender@example.com'
    msg['Subject'] = 'subject (issue{0!s})\r\n\r\n'.format(self.issue.key.id())
    self.assertRaises(views.InvalidIncomingEmailError,
                      views._process_incoming_mail, msg.as_string(),
                      'reply@example.com')

  def test_senders_becomes_reviewer(self):
    msg = Message()
    msg['From'] ='sender@example.com'
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg.set_payload('body')
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    issue = models.Issue.get_by_id(self.issue.key.id())  # re-fetch issue
    self.assertEqual(issue.reviewers, ['sender@example.com'])
    issue.reviewers = []
    issue.put()
    # try again with sender that has an account
    # we do this to handle CamelCase emails correctly
    models.Account.get_account_for_user(User('sender@example.com'))
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    issue = models.Issue.get_by_id(self.issue.key.id())
    self.assertEqual(issue.reviewers, ['sender@example.com'])

  def test_long_subjects(self):
    # multi-line subjects should be collapsed into a single line
    msg = Message()
    msg['Subject'] = ('foo '*30)+' (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg.set_payload('body')
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(len(imsg.subject.splitlines()), 1)

  def test_multipart(self):
    # Text first
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg.attach(MIMEText('body', 'plain'))
    msg.attach(MIMEText('ignore', 'html'))
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text, 'body')
    imsg.key.delete()
    # HTML first
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg.attach(MIMEText('ignore', 'html'))
    msg.attach(MIMEText('body', 'plain'))
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text, 'body')
    imsg.key.delete()
    # no text at all
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg.attach(MIMEText('ignore', 'html'))
    self.assertRaises(views.InvalidIncomingEmailError,
                      views._process_incoming_mail, msg.as_string(),
                      'reply@example.com')

  def test_mails_from_appengine(self):  # bounces
    msg = Message()
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg['X-Google-Appengine-App-Id'] = 'foo'
    self.assertRaises(views.InvalidIncomingEmailError,
                      views._process_incoming_mail, msg.as_string(),
                      'reply@exampe.com')

  def test_huge_body_is_truncated(self):  # see issue325
    msg = Message()
    msg['subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    msg.set_payload('1' * 600 * 1024)
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(len(imsg.text), 500 * 1024)
    self.assert_(imsg.text.endswith('... (message truncated)'))

  def test_charset(self):
    # make sure that incoming mails with non-ascii chars are handled correctly
    # see related http://code.google.com/p/googleappengine/issues/detail?id=2326
    jtxt = '\x1b$B%O%m!<%o!<%k%I!*\x1b(B'
    jcode = 'iso-2022-jp'
    msg = Message()
    msg.set_payload(jtxt, jcode)
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text.encode(jcode), jtxt)

  def test_encoding(self):
    # make sure that incoming mails with 8bit encoding are handled correctly.
    # see realted http://code.google.com/p/googleappengine/issues/detail?id=2383
    jtxt = '\x1b$B%O%m!<%o!<%k%I!*\x1b(B'
    jcode = 'iso-2022-jp'
    msg = Message()
    msg.set_payload(jtxt, jcode)
    msg['Subject'] = 'subject (issue{0!s})'.format(self.issue.key.id())
    msg['From'] = 'sender@example.com'
    del msg['Content-Transfer-Encoding']  # replace 7bit encoding
    msg['Content-Transfer-Encoding'] = '8bit'
    views._process_incoming_mail(msg.as_string(), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text.encode(jcode), jtxt)

  def test_missing_encoding(self):
    # make sure that incoming mails with missing encoding and
    # charset are handled correctly.
    body = 'Âfoo'
    msg = ('From: sender@example.com',
           'Subject: subject (issue{0!s})'.format(self.issue.key.id()),
           '',
           body)
    views._process_incoming_mail('\r\n'.join(msg), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text, u'Âfoo')
    imsg.key.delete()
    body = '\xf6'
    msg = ('From: sender@example.com',
           'Subject: subject (issue{0!s})'.format(self.issue.key.id()),
           '',
           body)
    views._process_incoming_mail('\r\n'.join(msg), 'reply@example.com')
    imsg = models.Message.query(ancestor=self.issue.key).get()
    self.assertEqual(imsg.text, u'\ufffd')


if __name__ == '__main__':
  unittest.main()
