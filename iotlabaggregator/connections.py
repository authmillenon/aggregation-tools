#! /usr/bin/env python
# -*- coding:utf-8 -*-

""" Aggregate multiple tcp connections """

import os
import sys
import asyncore
from asyncore import dispatcher_with_send
import threading
import socket
import signal

from iotlabaggregator import LOGGER


# Use dispatcher_with_send to correctly implement buffered sending
# either we get 100% CPU as 'writeable' is always 'True
# http://stackoverflow.com/questions/22423625/ \
#     python-asyncore-using-100-cpu-after-client-connects
# Found dispatcher_with_send in the asyncore code


class Connection(object, dispatcher_with_send):  # pylint:disable=R0904
    """
    Handle the connection to one node
    Data is managed with asyncore. So to work asyncore.loop() should be run.

    Child class should re-implement 'handle_data'
    """
    port = 20000

    def __init__(self, hostname, aggregator):
        super(Connection, self).__init__()
        dispatcher_with_send.__init__(self)
        self.hostname = hostname  # node identifier for the user
        self.data_buff = ''       # received data buffer
        self.aggregator = aggregator

    def handle_data(self, data):
        """ Dummy handle data """
        LOGGER.info("%s received %u bytes", self.hostname, len(data))
        return ''  # Remaining unprocessed data

    def start(self):
        """ Connects to node serial port """
        self.data_buff = ''      # reset data
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect((self.hostname, self.port))

    def handle_close(self):
        """ Close the connection and clear buffer """
        self.data_buff = ''
        LOGGER.error('%s;Connection closed', self.hostname)
        self.close()
        # remove itself from aggregator
        self.aggregator.pop(self.hostname, None)

    def handle_read(self):
        """ Append read bytes to buffer and run data handler. """
        self.data_buff += self.recv(8192)
        self.data_buff = self.handle_data(self.data_buff)

    def handle_error(self):
        """ Connection failed """
        LOGGER.error('%s;%r', self.hostname, sys.exc_info())


class Aggregator(dict):  # pylint:disable=too-many-public-methods
    """
    Create a dict of Connection from 'nodes_list'
    Each node is stored in the entry with it's node_id

    It as a thread that runs asyncore.loop() in the background.

    After init, it can be manipulated like a dict.
    """

    connection_class = Connection  # overriden in child class

    def __init__(self, nodes_list, *args, **kwargs):

        if not nodes_list:
            raise ValueError("%s: Empty nodes list %r" %
                             (self.__class__.__name__, nodes_list))
        super(Aggregator, self).__init__()
        self._running = False

        self.thread = threading.Thread(target=self._loop)
        # create all the Connections
        for node_url in nodes_list:
            node = self.connection_class(node_url, self, *args, **kwargs)
            self[node_url] = node

    def _loop(self):
        """ Run asyncore loop send SIGINT at the end to stop main process """
        asyncore.loop(timeout=1, use_poll=True)
        if self._running:  # Don't send signal if we are stopping
            LOGGER.info("Loop finished, all connection closed")
            os.kill(os.getpid(), signal.SIGINT)

    def start(self):
        """ Connect all nodes and run asyncore.loop in a thread """
        self._running = True
        for node in self.itervalues():
            node.start()
        self.thread.start()
        LOGGER.info("Aggregator started")

    def stop(self):
        """ Stop the nodes connection and stop asyncore.loop thread """
        LOGGER.info("Stopping")
        self._running = False
        for node in self.itervalues():
            node.close()
        self.thread.join()

    def run(self):  # pylint:disable=no-self-use
        """ Main function to run """
        try:
            signal.pause()
        except KeyboardInterrupt:
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _type, _value, _traceback):
        self.stop()

    def send_nodes(self, nodes_list, message):
        """ Send the `message` to `nodes_list` nodes
        If nodes_list is None, send to all nodes """
        if nodes_list is None:
            LOGGER.debug("Broadcast: %r", message)
            self.broadcast(message)
        else:
            LOGGER.debug("Send: %r to %r", message, nodes_list)
            for node in nodes_list:
                self._send(node, message)

    def _send(self, hostname, message):
        """ Safe send message to node """
        try:
            self[hostname].send(message)
        except KeyError:
            LOGGER.warning("Node not managed: %s", hostname)
        except socket.error:
            LOGGER.warning("Send failed: %s", hostname)

    def broadcast(self, message):
        """ Send a message to all the nodes serial links """
        for node in self.iterkeys():
            self._send(node, message)
