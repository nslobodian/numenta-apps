# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2015, Numenta, Inc.  Unless you have purchased from
# Numenta, Inc. a separate commercial license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

"""
Unittest of taurus/monitoring/monitor_dispatcher.py
"""
import unittest

from mock import Mock

from taurus.monitoring.monitor_dispatcher import MonitorDispatcher



class MonitorDispatcherTest(unittest.TestCase):


  def testIncompleteSubclassImplementation(self):

    # Disable pylint warning re: abc method not defined
    # pylint: disable=W0223
    class MyMonitor(MonitorDispatcher):
      pass # Class does not implement dispatchNotification

    self.assertRaises(TypeError, MyMonitor)


  # Disable "method could be a function" pylint warning
  # pylint: disable=R0201
  def testRegisterCheck(self):

    self.assertTrue(hasattr(MonitorDispatcher, "registerCheck"))

    checkInnerMock1 = Mock()
    checkInnerMock2 = Mock()

    class MyMonitor(MonitorDispatcher):

      def dispatchNotification(self, checkFn, excType, excValue, excTraceback):
        pass # abc protocol requires this in MonitorDispatcher subclasses

      @MonitorDispatcher.registerCheck
      # Disable "method could be a function" pylint warning
      # pylint: disable=R0201
      def myCheck1(self):
        checkInnerMock1()

      @MonitorDispatcher.registerCheck
      def myCheck2(self):
        checkInnerMock2()

    MyMonitor().checkAll()

    checkInnerMock1.assert_called_once_with()
    checkInnerMock2.assert_called_once_with()


  def testCheckAllSendsNotifications(self):

    self.assertTrue(hasattr(MonitorDispatcher, "checkAll"))

    dispatchNotificationMock = Mock()

    class MyMonitor(MonitorDispatcher):

      def dispatchNotification(self, checkFn, excType, excValue, excTraceback):
        dispatchNotificationMock(excType, excValue)

      @MonitorDispatcher.registerCheck
      def myCheck(self):
        raise Exception("myCheckFailed")

    myMonitor = MyMonitor()
    myMonitor.checkAll()

    (excType, excValue), _ = dispatchNotificationMock.call_args_list[0]

    self.assertIs(excType, Exception)
    self.assertIsInstance(excValue, Exception)
    self.assertEqual(excValue.message, "myCheckFailed")
