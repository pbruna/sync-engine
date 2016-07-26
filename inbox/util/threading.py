# -*- coding: utf-8 -*-
from inbox.models.thread import Thread
from sqlalchemy import desc
from sqlalchemy.orm import joinedload, load_only
from inbox.util.misc import cleanup_subject


MAX_THREAD_LENGTH = 500


def fetch_corresponding_thread(db_session, namespace_id, message):
    """fetch a thread matching the corresponding message. Returns None if
       there's no matching thread."""
    # FIXME: for performance reasons, we make the assumption that a reply
    # to a message always has a similar subject. This is only
    # right 95% of the time.
    clean_subject = cleanup_subject(message.subject)
    threads = db_session.query(Thread). \
        filter(Thread.namespace_id == namespace_id,
               Thread._cleaned_subject == clean_subject). \
        order_by(desc(Thread.id)). \
        options(load_only('id', 'discriminator'),
                joinedload(Thread.messages).load_only(
                    'from_addr', 'to_addr', 'bcc_addr', 'cc_addr'))

    for thread in threads:
        for match in thread.messages:
            # A lot of people BCC some address when sending mass
            # emails so ignore BCC.
            match_bcc = match.bcc_addr if match.bcc_addr else []
            message_bcc = message.bcc_addr if message.bcc_addr else []

            match_emails = [t[1] for t in match.participants
                            if t not in match_bcc]
            message_emails = [t[1] for t in message.participants
                              if t not in message_bcc]

            # A conversation takes place between two or more persons.
            # Are there more than two participants in common in this
            # thread? If yes, it's probably a related thread.
            match_participants_set = set(match_emails)
            message_participants_set = set(message_emails)

            if len(match_participants_set & message_participants_set) >= 2:
                # No need to loop through the rest of the messages
                # in the thread
                if len(thread.messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread

            # handle the case where someone is self-sending an email.
            if not message.from_addr or not message.to_addr:
                return

            match_from = [t[1] for t in match.from_addr]
            match_to = [t[1] for t in match.from_addr]
            message_from = [t[1] for t in message.from_addr]
            message_to = [t[1] for t in message.to_addr]

            if (len(message_to) == 1 and message_from == message_to and
                    match_from == match_to and message_to == match_from):
                # Check that we're not over max thread length in this case
                # No need to loop through the rest of the messages
                # in the thread.
                if len(thread.messages) >= MAX_THREAD_LENGTH:
                    break
                else:
                    return match.thread

    return


def _count_thread_messages(self, thread_id, db_session):
    count, = db_session.query(func.count(Message.id)). \
        filter(Message.thread_id == thread_id).one()
    return count

def add_message_to_thread(self, db_session, message_obj, raw_message):
    """Associate message_obj to the right Thread object, creating a new
    thread if necessary."""
    with db_session.no_autoflush:
        # Disable autoflush so we don't try to flush a message with null
        # thread_id.
        parent_thread = fetch_corresponding_thread(
            db_session, self.namespace_id, message_obj)
        construct_new_thread = True

        if parent_thread:
            # If there's a parent thread that isn't too long already,
            # add to it. Otherwise create a new thread.
            parent_message_count = self._count_thread_messages(
                parent_thread.id, db_session)
            if parent_message_count < MAX_THREAD_LENGTH:
                construct_new_thread = False

        if construct_new_thread:
            message_obj.thread = ImapThread.from_imap_message(
                db_session, self.namespace_id, message_obj)
        else:
            parent_thread.messages.append(message_obj)
