#!/usr/bin/env python
# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""End to end tests for ChromeDriver."""

import base64
import json
import math
import optparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib
import urllib2
import uuid

_THIS_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(1, os.path.join(_THIS_DIR, os.pardir))
sys.path.insert(1, os.path.join(_THIS_DIR, os.pardir, 'client'))
sys.path.insert(1, os.path.join(_THIS_DIR, os.pardir, 'server'))

import chrome_paths
import chromedriver
import unittest_util
import util
import server
from webelement import WebElement
import webserver

_TEST_DATA_DIR = os.path.join(chrome_paths.GetTestData(), 'chromedriver')

if util.IsLinux():
  sys.path.insert(0, os.path.join(chrome_paths.GetSrc(), 'third_party',
                                  'catapult', 'devil'))
  from devil.android import device_utils
  from devil.android import forwarder

  sys.path.insert(0, os.path.join(chrome_paths.GetSrc(), 'build', 'android'))
  import devil_chromium
  from pylib import constants


_NEGATIVE_FILTER = [
    # This test is too flaky on the bots, but seems to run perfectly fine
    # on developer workstations.
    'ChromeDriverTest.testEmulateNetworkConditionsNameSpeed',
    'ChromeDriverTest.testEmulateNetworkConditionsSpeed',
    # crbug.com/469947
    'ChromeDriverTest.testTouchPinch',
    'ChromeDriverTest.testReturningAFunctionInJavascript',
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1367
    'ChromeExtensionsCapabilityTest.testWaitsForExtensionToLoad',
    # TODO: re-enable tests when DevTools supports ScreenOrientation commands.
    'ChromeDriverAndroidTest.testScreenOrientation',
    'ChromeDriverAndroidTest.testMultipleScreenOrientationChanges',
    'ChromeDriverAndroidTest.testDeleteScreenOrientationManual',
    'ChromeDriverAndroidTest.testScreenOrientationAcrossMultipleTabs',
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1503
    'ChromeDriverTest.testShadowDomHover',
    'ChromeDriverTest.testMouseMoveTo',
    'ChromeDriverTest.testHoverOverElement',
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=833
    'ChromeDriverTest.testAlertOnNewWindow',
]

_VERSION_SPECIFIC_FILTER = {}
_VERSION_SPECIFIC_FILTER['HEAD'] = [
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1819
    'ChromeExtensionsCapabilityTest.testIFrameWithExtensionsSource',
]
_VERSION_SPECIFIC_FILTER['58'] = [
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1673
    'ChromeDriverPageLoadTimeoutTest.testPageLoadTimeoutCrossDomain',
]
_VERSION_SPECIFIC_FILTER['57'] = [
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1625
    'ChromeDriverTest.testWindowMaximize',
    'ChromeDriverTest.testWindowPosition',
    'ChromeDriverTest.testWindowSize',
    'ChromeExtensionsCapabilityTest.testCanInspectBackgroundPage',
    'ChromeExtensionsCapabilityTest.testCanLaunchApp',
    'MobileEmulationCapabilityTest.testDeviceMetricsWithStandardWidth',
]

_OS_SPECIFIC_FILTER = {}
_OS_SPECIFIC_FILTER['win'] = [
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=299
    'ChromeLogPathCapabilityTest.testChromeLogPath',
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=992
    'ChromeDownloadDirTest.testDownloadDirectoryOverridesExistingPreferences',
]
_OS_SPECIFIC_FILTER['linux'] = [
    # Xvfb doesn't support maximization.
    'ChromeDriverTest.testWindowMaximize',
]
_OS_SPECIFIC_FILTER['mac'] = []

_DESKTOP_NEGATIVE_FILTER = [
    # Desktop doesn't support touch (without --touch-events).
    'ChromeDriverTest.testTouchSingleTapElement',
    'ChromeDriverTest.testTouchDownMoveUpElement',
    'ChromeDriverTest.testTouchScrollElement',
    'ChromeDriverTest.testTouchDoubleTapElement',
    'ChromeDriverTest.testTouchLongPressElement',
    'ChromeDriverTest.testTouchFlickElement',
    'ChromeDriverTest.testTouchPinch',
    'ChromeDriverAndroidTest.*',
]


def _GetDesktopNegativeFilter(version_name):
  filter = _NEGATIVE_FILTER + _DESKTOP_NEGATIVE_FILTER
  os = util.GetPlatformName()
  if os in _OS_SPECIFIC_FILTER:
    filter += _OS_SPECIFIC_FILTER[os]
  if version_name in _VERSION_SPECIFIC_FILTER:
    filter += _VERSION_SPECIFIC_FILTER[version_name]
  return filter

_ANDROID_NEGATIVE_FILTER = {}
_ANDROID_NEGATIVE_FILTER['chrome'] = (
    _NEGATIVE_FILTER + [
        # TODO(chrisgao): fix hang of tab crash test on android.
        'ChromeDriverTest.testTabCrash',
        # Android doesn't support switches and extensions.
        'ChromeSwitchesCapabilityTest.*',
        'ChromeExtensionsCapabilityTest.*',
        'MobileEmulationCapabilityTest.*',
        'ChromeDownloadDirTest.*',
        # https://crbug.com/274650
        'ChromeDriverTest.testCloseWindow',
        # https://bugs.chromium.org/p/chromedriver/issues/detail?id=298
        'ChromeDriverTest.testWindowPosition',
        'ChromeDriverTest.testWindowSize',
        'ChromeDriverTest.testWindowMaximize',
        'ChromeLogPathCapabilityTest.testChromeLogPath',
        'RemoteBrowserTest.*',
        # Don't enable perf testing on Android yet.
        'PerfTest.testSessionStartTime',
        'PerfTest.testSessionStopTime',
        'PerfTest.testColdExecuteScript',
        # Android doesn't support multiple sessions on one device.
        'SessionHandlingTest.testGetSessions',
        # Android doesn't use the chrome://print dialog.
        'ChromeDriverTest.testCanSwitchToPrintPreviewDialog',
        # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1175
        'ChromeDriverTest.testChromeDriverSendLargeData',
        # Chrome 44+ for Android doesn't dispatch the dblclick event
        'ChromeDriverTest.testMouseDoubleClick',
        # Page cannot be loaded from file:// URI in Android unless it
        # is stored in device.
        'ChromeDriverTest.testCanClickAlertInIframes',
    ]
)
_ANDROID_NEGATIVE_FILTER['chrome_stable'] = (
    _ANDROID_NEGATIVE_FILTER['chrome'])
_ANDROID_NEGATIVE_FILTER['chrome_beta'] = (
    _ANDROID_NEGATIVE_FILTER['chrome'])
_ANDROID_NEGATIVE_FILTER['chromium'] = (
    _ANDROID_NEGATIVE_FILTER['chrome'] + [
        'ChromeDriverTest.testSwitchToWindow',
        # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1503
        'ChromeDriverTest.testShadowDomHover',
        'ChromeDriverTest.testMouseMoveTo',
        'ChromeDriverTest.testHoverOverElement',
        # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1478
        'ChromeDriverTest.testShouldHandleNewWindowLoadingProperly',
    ]
)
_ANDROID_NEGATIVE_FILTER['chromedriver_webview_shell'] = (
    _ANDROID_NEGATIVE_FILTER['chrome'] + [
        'ChromeLoggingCapabilityTest.testPerformanceLogger',
        'ChromeDriverTest.testShadowDom*',
        # WebView doesn't support emulating network conditions.
        'ChromeDriverTest.testEmulateNetworkConditions',
        'ChromeDriverTest.testEmulateNetworkConditionsNameSpeed',
        'ChromeDriverTest.testEmulateNetworkConditionsOffline',
        'ChromeDriverTest.testEmulateNetworkConditionsSpeed',
        'ChromeDriverTest.testEmulateNetworkConditionsName',
        # The WebView shell that we test against (on KitKat) does not yet
        # support Synthetic Gesture DevTools commands.
        # TODO(samuong): reenable when it does.
        'ChromeDriverTest.testHasTouchScreen',
        'ChromeDriverTest.testTouchScrollElement',
        'ChromeDriverTest.testTouchDoubleTapElement',
        'ChromeDriverTest.testTouchLongPressElement',
        'ChromeDriverTest.testTouchPinch',
        # WebView shell doesn't support popups or popup blocking.
        'ChromeDriverTest.testPopups',
        'ChromeDriverTest.testDontGoBackOrGoForward',
        # ChromeDriver WebView shell doesn't support multiple tabs.
        'ChromeDriverTest.testGetWindowHandles',
        'ChromeDriverTest.testSwitchToWindow',
        'ChromeDriverTest.testShouldHandleNewWindowLoadingProperly',
        'ChromeDriverTest.testGetLogOnClosedWindow',
        # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1295
        # TODO(gmanikpure): re-enable this test when we stop supporting
        # WebView on KitKat.
        'ChromeDriverTest.testGetUrlOnInvalidUrl',
        # The WebView shell that we test against (on KitKat) does not perform
        # cross-process navigations.
        # TODO(samuong): reenable when it does.
        'ChromeDriverPageLoadTimeoutTest.testPageLoadTimeoutCrossDomain',
        'ChromeDriverPageLoadTimeoutTest.'
            'testHistoryNavigationWithPageLoadTimeout',
        # Webview shell doesn't support Alerts.
        'ChromeDriverTest.testAlert',
        'ChromeDriverTest.testAlertOnNewWindow',
        'ChromeDesiredCapabilityTest.testUnexpectedAlertBehaviour',
        'ChromeDriverTest.testAlertHandlingOnPageUnload',
        'ChromeDriverTest.testClickElementAfterNavigation',
        'ChromeDriverTest.testGetLogOnWindowWithAlert',
        'ChromeDriverTest.testUnexpectedAlertOpenExceptionMessage',
    ]
)


class ChromeDriverBaseTest(unittest.TestCase):
  """Base class for testing chromedriver functionalities."""

  def __init__(self, *args, **kwargs):
    super(ChromeDriverBaseTest, self).__init__(*args, **kwargs)
    self._drivers = []

  def tearDown(self):
    for driver in self._drivers:
      try:
        driver.Quit()
      except:
        pass

  def CreateDriver(self, server_url=None, download_dir=None, **kwargs):
    if server_url is None:
      server_url = _CHROMEDRIVER_SERVER_URL

    android_package = None
    android_activity = None
    android_process = None
    if _ANDROID_PACKAGE_KEY:
      android_package = constants.PACKAGE_INFO[_ANDROID_PACKAGE_KEY].package
      if _ANDROID_PACKAGE_KEY == 'chromedriver_webview_shell':
        android_activity = constants.PACKAGE_INFO[_ANDROID_PACKAGE_KEY].activity
        android_process = '%s:main' % android_package

    driver = chromedriver.ChromeDriver(server_url,
                                       chrome_binary=_CHROME_BINARY,
                                       android_package=android_package,
                                       android_activity=android_activity,
                                       android_process=android_process,
                                       download_dir=download_dir,
                                       **kwargs)
    self._drivers += [driver]
    return driver

  def WaitForNewWindow(self, driver, old_handles, check_closed_windows=True):
    """Wait for at least one new window to show up in 20 seconds.

    Args:
      old_handles: Handles to all old windows before the new window is added.
      check_closed_windows: If True, assert that no windows are closed before
          the new window is added.

    Returns:
      Handle to a new window. None if timeout.
    """
    deadline = time.time() + 20
    while time.time() < deadline:
      handles = driver.GetWindowHandles()
      if check_closed_windows:
        self.assertTrue(set(old_handles).issubset(handles))
      new_handles = set(handles).difference(set(old_handles))
      if len(new_handles) > 0:
        return new_handles.pop()
      time.sleep(0.01)
    return None

  def WaitForCondition(self, predicate, timeout=5, timestep=0.1):
    """Wait for a condition to become true.

    Args:
      predicate: A function that returns a boolean value.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
      if predicate():
        return True
      time.sleep(timestep)
    return False


class ChromeDriverBaseTestWithWebServer(ChromeDriverBaseTest):

  @staticmethod
  def GlobalSetUp():
    ChromeDriverBaseTestWithWebServer._http_server = webserver.WebServer(
        chrome_paths.GetTestData())

  @staticmethod
  def GlobalTearDown():
    ChromeDriverBaseTestWithWebServer._http_server.Shutdown()

  @staticmethod
  def GetHttpUrlForFile(file_path):
    return ChromeDriverBaseTestWithWebServer._http_server.GetUrl() + file_path


class ChromeDriverTest(ChromeDriverBaseTestWithWebServer):
  """End to end tests for ChromeDriver."""

  @staticmethod
  def GlobalSetUp():
    ChromeDriverBaseTestWithWebServer.GlobalSetUp()
    ChromeDriverTest._sync_server = webserver.SyncWebServer()
    if _ANDROID_PACKAGE_KEY:
      ChromeDriverTest._device = device_utils.DeviceUtils.HealthyDevices()[0]
      http_host_port = ChromeDriverTest._http_server._server.server_port
      sync_host_port = ChromeDriverTest._sync_server._server.server_port
      forwarder.Forwarder.Map(
          [(http_host_port, http_host_port), (sync_host_port, sync_host_port)],
          ChromeDriverTest._device)

  @staticmethod
  def GlobalTearDown():
    if _ANDROID_PACKAGE_KEY:
      forwarder.Forwarder.UnmapAllDevicePorts(ChromeDriverTest._device)
    ChromeDriverBaseTestWithWebServer.GlobalTearDown()

  def setUp(self):
    self._driver = self.CreateDriver()

  def testStartStop(self):
    pass

  def testLoadUrl(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))

  def testGetCurrentWindowHandle(self):
    self._driver.GetCurrentWindowHandle()

  def testCloseWindow(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    old_handles = self._driver.GetWindowHandles()
    self._driver.FindElement('id', 'link').Click()
    new_window_handle = self.WaitForNewWindow(self._driver, old_handles)
    self.assertNotEqual(None, new_window_handle)
    self._driver.SwitchToWindow(new_window_handle)
    self.assertEquals(new_window_handle, self._driver.GetCurrentWindowHandle())
    self.assertRaises(chromedriver.NoSuchElement,
                      self._driver.FindElement, 'id', 'link')
    self._driver.CloseWindow()
    self.assertRaises(chromedriver.NoSuchWindow,
                      self._driver.GetCurrentWindowHandle)
    new_handles = self._driver.GetWindowHandles()
    for old_handle in old_handles:
      self.assertTrue(old_handle in new_handles)
    for handle in new_handles:
      self._driver.SwitchToWindow(handle)
      self.assertEquals(handle, self._driver.GetCurrentWindowHandle())
      self._driver.CloseWindow()

  def testGetWindowHandles(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    old_handles = self._driver.GetWindowHandles()
    self._driver.FindElement('id', 'link').Click()
    self.assertNotEqual(None, self.WaitForNewWindow(self._driver, old_handles))

  def testGetWindowHandlesInPresenceOfSharedWorker(self):
    self._driver.Load(
        self.GetHttpUrlForFile('/chromedriver/shared_worker.html'))
    old_handles = self._driver.GetWindowHandles()

  def testSwitchToWindow(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    self.assertEquals(
        1, self._driver.ExecuteScript('window.name = "oldWindow"; return 1;'))
    window1_handle = self._driver.GetCurrentWindowHandle()
    old_handles = self._driver.GetWindowHandles()
    self._driver.FindElement('id', 'link').Click()
    new_window_handle = self.WaitForNewWindow(self._driver, old_handles)
    self.assertNotEqual(None, new_window_handle)
    self._driver.SwitchToWindow(new_window_handle)
    self.assertEquals(new_window_handle, self._driver.GetCurrentWindowHandle())
    self.assertRaises(chromedriver.NoSuchElement,
                      self._driver.FindElement, 'id', 'link')
    self._driver.SwitchToWindow('oldWindow')
    self.assertEquals(window1_handle, self._driver.GetCurrentWindowHandle())

  def testEvaluateScript(self):
    self.assertEquals(1, self._driver.ExecuteScript('return 1'))
    self.assertEquals(None, self._driver.ExecuteScript(''))

  def testEvaluateScriptWithArgs(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    script = ('document.body.innerHTML = "<div>b</div><div>c</div>";'
              'return {stuff: document.querySelectorAll("div")};')
    stuff = self._driver.ExecuteScript(script)['stuff']
    script = 'return arguments[0].innerHTML + arguments[1].innerHTML'
    self.assertEquals(
        'bc', self._driver.ExecuteScript(script, stuff[0], stuff[1]))

  def testEvaluateInvalidScript(self):
    self.assertRaises(chromedriver.ChromeDriverException,
                      self._driver.ExecuteScript, '{{{')

  def testExecuteAsyncScript(self):
    self._driver.SetTimeout('script', 3000)
    self.assertRaises(
        chromedriver.ScriptTimeout,
        self._driver.ExecuteAsyncScript,
        'var callback = arguments[0];'
        'setTimeout(function(){callback(1);}, 10000);')
    self.assertEquals(
        2,
        self._driver.ExecuteAsyncScript(
            'var callback = arguments[0];'
            'setTimeout(function(){callback(2);}, 300);'))

  def testSwitchToFrame(self):
    self._driver.ExecuteScript(
        'var frame = document.createElement("iframe");'
        'frame.id="id";'
        'frame.name="name";'
        'document.body.appendChild(frame);')
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))
    self._driver.SwitchToFrame('id')
    self.assertTrue(self._driver.ExecuteScript('return window.top != window'))
    self._driver.SwitchToMainFrame()
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))
    self._driver.SwitchToFrame('name')
    self.assertTrue(self._driver.ExecuteScript('return window.top != window'))
    self._driver.SwitchToMainFrame()
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))
    self._driver.SwitchToFrameByIndex(0)
    self.assertTrue(self._driver.ExecuteScript('return window.top != window'))
    self._driver.SwitchToMainFrame()
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))
    self._driver.SwitchToFrame(self._driver.FindElement('tag name', 'iframe'))
    self.assertTrue(self._driver.ExecuteScript('return window.top != window'))

  def testSwitchToParentFrame(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/nested.html'))
    self.assertTrue('One' in self._driver.GetPageSource())
    self._driver.SwitchToFrameByIndex(0)
    self.assertTrue('Two' in self._driver.GetPageSource())
    self._driver.SwitchToFrameByIndex(0)
    self.assertTrue('Three' in self._driver.GetPageSource())
    self._driver.SwitchToParentFrame()
    self.assertTrue('Two' in self._driver.GetPageSource())
    self._driver.SwitchToParentFrame()
    self.assertTrue('One' in self._driver.GetPageSource())

  def testSwitchToNestedFrame(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/nested_frameset.html'))
    self._driver.SwitchToFrameByIndex(0)
    self.assertTrue(self._driver.FindElement("id", "link").IsDisplayed())
    self._driver.SwitchToMainFrame()
    self._driver.SwitchToFrame('2Frame')
    self.assertTrue(self._driver.FindElement("id", "l1").IsDisplayed())
    self._driver.SwitchToMainFrame()
    self._driver.SwitchToFrame('fourth_frame')
    self.assertTrue('One' in self._driver.GetPageSource())
    self._driver.SwitchToMainFrame()
    self._driver.SwitchToFrameByIndex(4)
    self.assertTrue(self._driver.FindElement("id", "aa1").IsDisplayed())

  def testExecuteInRemovedFrame(self):
    self._driver.ExecuteScript(
        'var frame = document.createElement("iframe");'
        'frame.id="id";'
        'frame.name="name";'
        'document.body.appendChild(frame);'
        'window.addEventListener("message",'
        '    function(event) { document.body.removeChild(frame); });')
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))
    self._driver.SwitchToFrame('id')
    self.assertTrue(self._driver.ExecuteScript('return window.top != window'))
    self._driver.ExecuteScript('parent.postMessage("remove", "*");')
    self.assertTrue(self._driver.ExecuteScript('return window.top == window'))

  def testGetTitle(self):
    script = 'document.title = "title"; return 1;'
    self.assertEquals(1, self._driver.ExecuteScript(script))
    self.assertEquals('title', self._driver.GetTitle())

  def testGetPageSource(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    self.assertTrue('Link to empty.html' in self._driver.GetPageSource())

  def testFindElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>a</div><div>b</div>";')
    self.assertTrue(
        isinstance(self._driver.FindElement('tag name', 'div'), WebElement))

  def testNoSuchElementExceptionMessage(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>a</div><div>b</div>";')
    self.assertRaisesRegexp(chromedriver.NoSuchElement,
                            'no such element: Unable '
                            'to locate element: {"method":"tag name",'
                            '"selector":"divine"}',
                            self._driver.FindElement,
                            'tag name', 'divine')

  def testUnexpectedAlertOpenExceptionMessage(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript('window.alert("Hi");')
    self.assertRaisesRegexp(chromedriver.UnexpectedAlertOpen,
                            'unexpected alert open: {Alert text : Hi}',
                            self._driver.FindElement, 'tag name', 'divine')

  def testFindElements(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>a</div><div>b</div>";')
    divs = self._driver.FindElements('tag name', 'div')
    self.assertTrue(isinstance(divs, list))
    self.assertEquals(2, len(divs))
    for div in divs:
      self.assertTrue(isinstance(div, WebElement))

  def testFindChildElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div><br><br></div><div><a></a></div>";')
    element = self._driver.FindElement('tag name', 'div')
    self.assertTrue(
        isinstance(element.FindElement('tag name', 'br'), WebElement))

  def testFindChildElements(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div><br><br></div><div><br></div>";')
    element = self._driver.FindElement('tag name', 'div')
    brs = element.FindElements('tag name', 'br')
    self.assertTrue(isinstance(brs, list))
    self.assertEquals(2, len(brs))
    for br in brs:
      self.assertTrue(isinstance(br, WebElement))

  def testHoverOverElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.addEventListener("mouseover", function() {'
        '  document.body.appendChild(document.createElement("br"));'
        '});'
        'return div;')
    div.HoverOver()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testClickElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.addEventListener("click", function() {'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    div.Click()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testClickElementInSubFrame(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/frame_test.html'))
    frame = self._driver.FindElement('tag name', 'iframe')
    self._driver.SwitchToFrame(frame)
    # Test clicking element in the sub frame.
    self.testClickElement()

  def testClickElementAfterNavigation(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/link_nav.html'))
    link = self._driver.FindElement('id', 'l1')
    link.Click()
    alert_button = self._driver.FindElement('id', 'aa1')
    alert_button.Click()
    self.assertTrue(self._driver.IsAlertOpen())

  def testPageLoadStrategyIsNormalByDefault(self):
    self.assertEquals('normal',
                      self._driver.capabilities['pageLoadStrategy'])

  def testClearElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    text = self._driver.ExecuteScript(
        'document.body.innerHTML = \'<input type="text" value="abc">\';'
        'return document.getElementsByTagName("input")[0];')
    value = self._driver.ExecuteScript('return arguments[0].value;', text)
    self.assertEquals('abc', value)
    text.Clear()
    value = self._driver.ExecuteScript('return arguments[0].value;', text)
    self.assertEquals('', value)

  def testSendKeysToElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    text = self._driver.ExecuteScript(
        'document.body.innerHTML = \'<input type="text">\';'
        'var input = document.getElementsByTagName("input")[0];'
        'input.addEventListener("change", function() {'
        '  document.body.appendChild(document.createElement("br"));'
        '});'
        'return input;')
    text.SendKeys('0123456789+-*/ Hi')
    text.SendKeys(', there!')
    value = self._driver.ExecuteScript('return arguments[0].value;', text)
    self.assertEquals('0123456789+-*/ Hi, there!', value)

  def testSendingTabKeyMovesToNextInputElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/two_inputs.html'))
    first = self._driver.FindElement('id', 'first')
    second = self._driver.FindElement('id', 'second')
    first.Click()
    self._driver.SendKeys('snoopy')
    self._driver.SendKeys(u'\uE004')
    self._driver.SendKeys('prickly pete')
    self.assertEquals('snoopy', self._driver.ExecuteScript(
        'return arguments[0].value;', first))
    self.assertEquals('prickly pete', self._driver.ExecuteScript(
        'return arguments[0].value;', second))

  def testGetElementAttribute(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/attribute_colon_test.html'))
    elem = self._driver.FindElement("name", "phones")
    self.assertEquals('3', elem.GetAttribute('size'))

  def testGetElementSpecialCharAttribute(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/attribute_colon_test.html'))
    elem = self._driver.FindElement("name", "phones")
    self.assertEquals('colonvalue', elem.GetAttribute('ext:qtip'))

  def testGetCurrentUrl(self):
    url = self.GetHttpUrlForFile('/chromedriver/frame_test.html')
    self._driver.Load(url)
    self.assertEquals(url, self._driver.GetCurrentUrl())
    self._driver.SwitchToFrame(self._driver.FindElement('tagName', 'iframe'))
    self.assertEquals(url, self._driver.GetCurrentUrl())

  def testGoBackAndGoForward(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.GoBack()
    self._driver.GoForward()

  def testDontGoBackOrGoForward(self):
    # We need to run this test in a new tab so that it is isolated from previous
    # test runs.
    old_windows = self._driver.GetWindowHandles()
    self._driver.ExecuteScript('window.open("about:blank")')
    new_window = self.WaitForNewWindow(self._driver, old_windows)
    self._driver.SwitchToWindow(new_window)
    self.assertEquals('about:blank', self._driver.GetCurrentUrl())
    self._driver.GoBack()
    self.assertEquals('about:blank', self._driver.GetCurrentUrl())
    self._driver.GoForward()
    self.assertEquals('about:blank', self._driver.GetCurrentUrl())

  def testBackNavigationAfterClickElement(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/link_nav.html'))
    link = self._driver.FindElement('id', 'l1')
    link.Click()
    self._driver.GoBack()
    self.assertNotEqual('data:,', self._driver.GetCurrentUrl())
    self.assertEquals(self.GetHttpUrlForFile('/chromedriver/link_nav.html'),
                      self._driver.GetCurrentUrl())

  def testAlertHandlingOnPageUnload(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript('window.onbeforeunload=function(){return true}')
    self._driver.FindElement('tag name', 'body').Click()
    self._driver.GoBack()
    self.assertTrue(self._driver.IsAlertOpen())
    self._driver.HandleAlert(True)
    self.assertFalse(self._driver.IsAlertOpen())

  def testRefresh(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.Refresh()

  def testMouseMoveTo(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.style["width"] = "100px";'
        'div.style["height"] = "100px";'
        'div.addEventListener("mouseover", function() {'
        '  var div = document.getElementsByTagName("div")[0];'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    self._driver.MouseMoveTo(div, 10, 10)
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testMoveToElementAndClick(self):
    # This page gets rendered differently depending on which platform the test
    # is running on, and what window size is being used. So we need to do some
    # sanity checks to make sure that the <a> element is split across two lines
    # of text.
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/multiline.html'))

    # Check that link element spans two lines and that the first ClientRect is
    # above the second.
    link = self._driver.FindElements('tag name', 'a')[0]
    client_rects = self._driver.ExecuteScript(
        'return arguments[0].getClientRects();', link)
    self.assertEquals(2, len(client_rects))
    self.assertTrue(client_rects[0]['bottom'] <= client_rects[1]['top'])

    # Check that the center of the link's bounding ClientRect is outside the
    # element.
    bounding_client_rect = self._driver.ExecuteScript(
        'return arguments[0].getBoundingClientRect();', link)
    center = bounding_client_rect['left'] + bounding_client_rect['width'] / 2
    self.assertTrue(client_rects[1]['right'] < center)
    self.assertTrue(center < client_rects[0]['left'])

    self._driver.MouseMoveTo(link)
    self._driver.MouseClick()
    self.assertTrue(self._driver.GetCurrentUrl().endswith('#top'))

  def testMouseClick(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.style["width"] = "100px";'
        'div.style["height"] = "100px";'
        'div.addEventListener("click", function() {'
        '  var div = document.getElementsByTagName("div")[0];'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    self._driver.MouseMoveTo(div)
    self._driver.MouseClick()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testMouseButtonDownAndUp(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.style["width"] = "100px";'
        'div.style["height"] = "100px";'
        'div.addEventListener("mousedown", function() {'
        '  var div = document.getElementsByTagName("div")[0];'
        '  div.innerHTML="new1<br>";'
        '});'
        'div.addEventListener("mouseup", function() {'
        '  var div = document.getElementsByTagName("div")[0];'
        '  div.innerHTML="new2<a></a>";'
        '});')
    self._driver.MouseMoveTo(None, 50, 50)
    self._driver.MouseButtonDown()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))
    self._driver.MouseButtonUp()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'a')))

  def testMouseDoubleClick(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.style["width"] = "100px";'
        'div.style["height"] = "100px";'
        'div.addEventListener("dblclick", function() {'
        '  var div = document.getElementsByTagName("div")[0];'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    self._driver.MouseMoveTo(div, 1, 1)
    self._driver.MouseDoubleClick()
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testAlert(self):
    self.assertFalse(self._driver.IsAlertOpen())
    self._driver.ExecuteScript('window.confirmed = confirm(\'HI\');')
    self.assertTrue(self._driver.IsAlertOpen())
    self.assertEquals('HI', self._driver.GetAlertMessage())
    self._driver.HandleAlert(False)
    self.assertFalse(self._driver.IsAlertOpen())
    self.assertEquals(False,
                      self._driver.ExecuteScript('return window.confirmed'))

  def testAlertOnNewWindow(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    old_windows = self._driver.GetWindowHandles()
    self._driver.ExecuteScript("window.open('%s')" %
        self.GetHttpUrlForFile('/chromedriver/alert_onload.html'))
    new_window = self.WaitForNewWindow(self._driver, old_windows)
    self.assertNotEqual(None, new_window)
    self._driver.SwitchToWindow(new_window)
    self.assertTrue(self._driver.IsAlertOpen())
    self._driver.HandleAlert(False)
    self.assertFalse(self._driver.IsAlertOpen())

  def testShouldHandleNewWindowLoadingProperly(self):
    """Tests that ChromeDriver determines loading correctly for new windows."""
    self._http_server.SetDataForPath(
        '/newwindow',
        """
        <html>
        <body>
        <a href='%s' target='_blank'>new window/tab</a>
        </body>
        </html>""" % self._sync_server.GetUrl())
    self._driver.Load(self._http_server.GetUrl() + '/newwindow')
    old_windows = self._driver.GetWindowHandles()
    self._driver.FindElement('tagName', 'a').Click()
    new_window = self.WaitForNewWindow(self._driver, old_windows)
    self.assertNotEqual(None, new_window)

    self.assertFalse(self._driver.IsLoading())
    self._driver.SwitchToWindow(new_window)
    self.assertTrue(self._driver.IsLoading())
    self._sync_server.RespondWithContent('<html>new window</html>')
    self._driver.ExecuteScript('return 1')  # Shouldn't hang.

  def testPopups(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    old_handles = self._driver.GetWindowHandles()
    self._driver.ExecuteScript('window.open("about:blank")')
    new_window_handle = self.WaitForNewWindow(self._driver, old_handles)
    self.assertNotEqual(None, new_window_handle)

  def testNoSuchFrame(self):
    self.assertRaises(chromedriver.NoSuchFrame,
                      self._driver.SwitchToFrame, 'nosuchframe')
    self.assertRaises(chromedriver.NoSuchFrame,
                      self._driver.SwitchToFrame,
                      self._driver.FindElement('tagName', 'body'))

  def testWindowPosition(self):
    position = self._driver.GetWindowPosition()
    self._driver.SetWindowPosition(position[0], position[1])
    self.assertEquals(position, self._driver.GetWindowPosition())

    # Resize so the window isn't moved offscreen.
    # See https://bugs.chromium.org/p/chromedriver/issues/detail?id=297.
    self._driver.SetWindowSize(300, 300)

    self._driver.SetWindowPosition(100, 200)
    self.assertEquals([100, 200], self._driver.GetWindowPosition())

  def testWindowSize(self):
    size = self._driver.GetWindowSize()
    self._driver.SetWindowSize(size[0], size[1])
    self.assertEquals(size, self._driver.GetWindowSize())

    self._driver.SetWindowSize(600, 400)
    self.assertEquals([600, 400], self._driver.GetWindowSize())

  def testWindowMaximize(self):
    self._driver.SetWindowPosition(100, 200)
    self._driver.SetWindowSize(500, 300)
    self._driver.MaximizeWindow()

    self.assertNotEqual([100, 200], self._driver.GetWindowPosition())
    self.assertNotEqual([500, 300], self._driver.GetWindowSize())
    # Set size first so that the window isn't moved offscreen.
    # See https://bugs.chromium.org/p/chromedriver/issues/detail?id=297.
    self._driver.SetWindowSize(600, 400)
    self._driver.SetWindowPosition(100, 200)
    self.assertEquals([100, 200], self._driver.GetWindowPosition())
    self.assertEquals([600, 400], self._driver.GetWindowSize())

  def testConsoleLogSources(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/console_log.html'))
    logs = self._driver.GetLog('browser')

    self.assertEqual('javascript', logs[0]['source'])
    self.assertTrue('TypeError' in logs[0]['message'])

    self.assertEqual('network', logs[1]['source'])
    self.assertTrue('nonexistent.png' in logs[1]['message'])
    self.assertTrue('404' in logs[1]['message'])

    # Sometimes, we also get an error for a missing favicon.
    if len(logs) > 2:
      self.assertEqual('network', logs[2]['source'])
      self.assertTrue('favicon.ico' in logs[2]['message'])
      self.assertTrue('404' in logs[2]['message'])
      self.assertEqual(3, len(logs))
    else:
      self.assertEqual(2, len(logs))

  def testPendingConsoleLog(self):
    new_logs = [""]
    def GetPendingLogs(driver):
      new_logs[0] = driver.GetLog('browser')
      return new_logs[0]

    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/pending_console_log.html'))
    logs = self._driver.GetLog('browser')
    self.assertEqual('console-api', logs[0]['source'])
    self.assertTrue('InitialError' in logs[0]['message'])

    self.WaitForCondition(lambda: len(GetPendingLogs(self._driver)) > 0 , 11)
    self.assertEqual('console-api', new_logs[0][0]['source'])
    self.assertTrue('RepeatedError' in new_logs[0][0]['message'])

  def testGetLogOnClosedWindow(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    old_handles = self._driver.GetWindowHandles()
    self._driver.FindElement('id', 'link').Click()
    self.WaitForNewWindow(self._driver, old_handles)
    self._driver.CloseWindow()
    try:
      self._driver.GetLog('browser')
    except chromedriver.ChromeDriverException as e:
      self.fail('exception while calling GetLog on a closed tab: ' + e.message)

  def testGetLogOnWindowWithAlert(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.ExecuteScript('alert("alert!");')
    try:
      self._driver.GetLog('browser')
    except Exception as e:
      self.fail(e.message)

  def testAutoReporting(self):
    self.assertFalse(self._driver.IsAutoReporting())
    self._driver.SetAutoReporting(True)
    self.assertTrue(self._driver.IsAutoReporting())
    url = self.GetHttpUrlForFile('/chromedriver/console_log.html')
    self.assertRaisesRegexp(
        chromedriver.UnknownError,
        ".*Uncaught TypeError: Cannot read property 'y' of undefined.*",
        self._driver.Load, url)

  def testContextMenuEventFired(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/context_menu.html'))
    self._driver.MouseMoveTo(self._driver.FindElement('tagName', 'div'))
    self._driver.MouseClick(2)
    self.assertTrue(self._driver.ExecuteScript('return success'))

  def testHasFocusOnStartup(self):
    # Some pages (about:blank) cause Chrome to put the focus in URL bar.
    # This breaks tests depending on focus.
    self.assertTrue(self._driver.ExecuteScript('return document.hasFocus()'))

  def testTabCrash(self):
    # If a tab is crashed, the session will be deleted.
    # When 31 is released, will reload the tab instead.
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=547
    self.assertRaises(chromedriver.UnknownError,
                      self._driver.Load, 'chrome://crash')
    self.assertRaises(chromedriver.NoSuchSession,
                      self._driver.GetCurrentUrl)

  def testDoesntHangOnDebugger(self):
    self._driver.Load('about:blank')
    self._driver.ExecuteScript('debugger;')

  def testMobileEmulationDisabledByDefault(self):
    self.assertFalse(self._driver.capabilities['mobileEmulationEnabled'])

  def testChromeDriverSendLargeData(self):
    script = 's = ""; for (i = 0; i < 10e6; i++) s += "0"; return s;'
    lots_of_data = self._driver.ExecuteScript(script)
    self.assertEquals('0'.zfill(int(10e6)), lots_of_data)

  def testEmulateNetworkConditions(self):
    # Network conditions must be set before it can be retrieved.
    self.assertRaises(chromedriver.UnknownError,
                      self._driver.GetNetworkConditions)

    # DSL: 2Mbps throughput, 5ms RTT
    latency = 5
    throughput = 2048 * 1024
    self._driver.SetNetworkConditions(latency, throughput, throughput)

    network = self._driver.GetNetworkConditions()
    self.assertEquals(latency, network['latency']);
    self.assertEquals(throughput, network['download_throughput']);
    self.assertEquals(throughput, network['upload_throughput']);
    self.assertEquals(False, network['offline']);

    # Network Conditions again cannot be retrieved after they've been deleted.
    self._driver.DeleteNetworkConditions()
    self.assertRaises(chromedriver.UnknownError,
                      self._driver.GetNetworkConditions)

  def testEmulateNetworkConditionsName(self):
    # DSL: 2Mbps throughput, 5ms RTT
    # latency = 5
    # throughput = 2048 * 1024
    self._driver.SetNetworkConditionsName('DSL')

    network = self._driver.GetNetworkConditions()
    self.assertEquals(5, network['latency']);
    self.assertEquals(2048*1024, network['download_throughput']);
    self.assertEquals(2048*1024, network['upload_throughput']);
    self.assertEquals(False, network['offline']);

  def testEmulateNetworkConditionsSpeed(self):
    # Warm up the browser.
    self._http_server.SetDataForPath(
        '/', "<html><body>blank</body></html>")
    self._driver.Load(self._http_server.GetUrl() + '/')

    # DSL: 2Mbps throughput, 5ms RTT
    latency = 5
    throughput_kbps = 2048
    throughput = throughput_kbps * 1024
    self._driver.SetNetworkConditions(latency, throughput, throughput)

    _32_bytes = " 0 1 2 3 4 5 6 7 8 9 A B C D E F"
    _1_megabyte = _32_bytes * 32768
    self._http_server.SetDataForPath(
        '/1MB',
        "<html><body>%s</body></html>" % _1_megabyte)
    start = time.time()
    self._driver.Load(self._http_server.GetUrl() + '/1MB')
    finish = time.time()
    duration = finish - start
    actual_throughput_kbps = 1024 / duration
    self.assertLessEqual(actual_throughput_kbps, throughput_kbps * 1.5)
    self.assertGreaterEqual(actual_throughput_kbps, throughput_kbps / 1.5)

  def testEmulateNetworkConditionsNameSpeed(self):
    # Warm up the browser.
    self._http_server.SetDataForPath(
        '/', "<html><body>blank</body></html>")
    self._driver.Load(self._http_server.GetUrl() + '/')

    # DSL: 2Mbps throughput, 5ms RTT
    throughput_kbps = 2048
    throughput = throughput_kbps * 1024
    self._driver.SetNetworkConditionsName('DSL')

    _32_bytes = " 0 1 2 3 4 5 6 7 8 9 A B C D E F"
    _1_megabyte = _32_bytes * 32768
    self._http_server.SetDataForPath(
        '/1MB',
        "<html><body>%s</body></html>" % _1_megabyte)
    start = time.time()
    self._driver.Load(self._http_server.GetUrl() + '/1MB')
    finish = time.time()
    duration = finish - start
    actual_throughput_kbps = 1024 / duration
    self.assertLessEqual(actual_throughput_kbps, throughput_kbps * 1.5)
    self.assertGreaterEqual(actual_throughput_kbps, throughput_kbps / 1.5)

  def testEmulateNetworkConditionsOffline(self):
    # A workaround for crbug.com/177511; when setting offline, the throughputs
    # must be 0.
    self._driver.SetNetworkConditions(0, 0, 0, offline=True)
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    # The "X is not available" title is set after the page load event fires, so
    # we have to explicitly wait for this to change. We can't rely on the
    # navigation tracker to block the call to Load() above.
    self.WaitForCondition(lambda: 'is not available' in self._driver.GetTitle())

  def testSendCommand(self):
    """Sends a custom command to the DevTools debugger"""
    params = {}
    res = self._driver.SendCommandAndGetResult('CSS.enable', params)
    self.assertEqual({}, res)

  def testSendCommandNoParams(self):
    """Sends a custom command to the DevTools debugger without params"""
    self.assertRaisesRegexp(
            chromedriver.UnknownError, "params not passed",
            self._driver.SendCommandAndGetResult, 'CSS.enable', None)

  def testSendCommandAndGetResult(self):
    """Sends a custom command to the DevTools debugger and gets the result"""
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/page_test.html'))
    params = {}
    document = self._driver.SendCommandAndGetResult('DOM.getDocument', params)
    self.assertTrue('root' in document)

  def testShadowDomFindElementWithSlashDeep(self):
    """Checks that chromedriver can find elements in a shadow DOM using /deep/
    css selectors."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    self.assertTrue(self._driver.FindElement("css", "* /deep/ #olderTextBox"))

  def testShadowDomFindChildElement(self):
    """Checks that chromedriver can find child elements from a shadow DOM
    element."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderChildDiv")
    self.assertTrue(elem.FindElement("id", "olderTextBox"))

  def testShadowDomFindElementFailsFromRootWithoutSlashDeep(self):
    """Checks that chromedriver can't find elements in a shadow DOM without
    /deep/."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    # can't find element from the root without /deep/
    with self.assertRaises(chromedriver.NoSuchElement):
      self._driver.FindElement("id", "#olderTextBox")

  def testShadowDomFindElementFailsBetweenShadowRoots(self):
    """Checks that chromedriver can't find elements in other shadow DOM
    trees."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #youngerChildDiv")
    with self.assertRaises(chromedriver.NoSuchElement):
      elem.FindElement("id", "#olderTextBox")

  def testShadowDomText(self):
    """Checks that chromedriver can find extract the text from a shadow DOM
    element."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderHeading")
    self.assertEqual("Older Child", elem.GetText())

  def testShadowDomSendKeys(self):
    """Checks that chromedriver can call SendKeys on a shadow DOM element."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderTextBox")
    elem.SendKeys("bar")
    self.assertEqual("foobar", self._driver.ExecuteScript(
        'return document.querySelector("* /deep/ #olderTextBox").value;'))

  def testShadowDomClear(self):
    """Checks that chromedriver can call Clear on a shadow DOM element."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderTextBox")
    elem.Clear()
    self.assertEqual("", self._driver.ExecuteScript(
        'return document.querySelector("* /deep/ #olderTextBox").value;'))

  def testShadowDomClick(self):
    """Checks that chromedriver can call Click on an element in a shadow DOM."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderButton")
    elem.Click()
    # the button's onClicked handler changes the text box's value
    self.assertEqual("Button Was Clicked", self._driver.ExecuteScript(
        'return document.querySelector("* /deep/ #olderTextBox").value;'))

  def testShadowDomHover(self):
    """Checks that chromedriver can call HoverOver on an element in a
    shadow DOM."""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderButton")
    elem.HoverOver()
    # the button's onMouseOver handler changes the text box's value
    self.assertEqual("Button Was Hovered Over", self._driver.ExecuteScript(
        'return document.querySelector("* /deep/ #olderTextBox").value;'))

  def testShadowDomStaleReference(self):
    """Checks that trying to manipulate shadow DOM elements that are detached
    from the document raises a StaleElementReference exception"""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderButton")
    self._driver.ExecuteScript(
        'document.querySelector("#outerDiv").innerHTML="<div/>";')
    with self.assertRaises(chromedriver.StaleElementReference):
      elem.Click()

  def testShadowDomDisplayed(self):
    """Checks that trying to manipulate shadow DOM elements that are detached
    from the document raises a StaleElementReference exception"""
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/shadow_dom_test.html'))
    elem = self._driver.FindElement("css", "* /deep/ #olderButton")
    self.assertTrue(elem.IsDisplayed())
    self._driver.ExecuteScript(
        'document.querySelector("#outerDiv").style.display="None";')
    self.assertFalse(elem.IsDisplayed())

  def testTouchSingleTapElement(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    target = self._driver.FindElement('id', 'target')
    target.SingleTap()
    events = self._driver.FindElement('id', 'events')
    self.assertEquals('events: touchstart touchend', events.GetText())

  def testTouchDownMoveUpElement(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    target = self._driver.FindElement('id', 'target')
    location = target.GetLocation()
    self._driver.TouchDown(location['x'], location['y'])
    events = self._driver.FindElement('id', 'events')
    self.assertEquals('events: touchstart', events.GetText())
    self._driver.TouchMove(location['x'] + 1, location['y'] + 1)
    self.assertEquals('events: touchstart touchmove', events.GetText())
    self._driver.TouchUp(location['x'] + 1, location['y'] + 1)
    self.assertEquals('events: touchstart touchmove touchend', events.GetText())

  def testTouchScrollElement(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    scroll_left = 'return document.body.scrollLeft;'
    scroll_top = 'return document.body.scrollTop;'
    self.assertEquals(0, self._driver.ExecuteScript(scroll_left))
    self.assertEquals(0, self._driver.ExecuteScript(scroll_top))
    target = self._driver.FindElement('id', 'target')
    self._driver.TouchScroll(target, 47, 53)
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1179
    self.assertAlmostEqual(47, self._driver.ExecuteScript(scroll_left), delta=1)
    self.assertAlmostEqual(53, self._driver.ExecuteScript(scroll_top), delta=1)

  def testTouchDoubleTapElement(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    target = self._driver.FindElement('id', 'target')
    target.DoubleTap()
    events = self._driver.FindElement('id', 'events')
    self.assertEquals('events: touchstart touchend touchstart touchend',
        events.GetText())

  def testTouchLongPressElement(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    target = self._driver.FindElement('id', 'target')
    target.LongPress()
    events = self._driver.FindElement('id', 'events')
    self.assertEquals('events: touchstart touchcancel', events.GetText())

  def testTouchFlickElement(self):
    dx = 3
    dy = 4
    speed = 5
    flickTouchEventsPerSecond = 30
    moveEvents = int(
        math.sqrt(dx * dx + dy * dy) * flickTouchEventsPerSecond / speed)
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    div = self._driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.addEventListener("touchstart", function() {'
        '  div.innerHTML = "preMove0";'
        '});'
        'div.addEventListener("touchmove", function() {'
        '  res = div.innerHTML.match(/preMove(\d+)/);'
        '  if (res != null) {'
        '    div.innerHTML = "preMove" + (parseInt(res[1], 10) + 1);'
        '  }'
        '});'
        'div.addEventListener("touchend", function() {'
        '  if (div.innerHTML == "preMove' + str(moveEvents) + '") {'
        '    div.innerHTML = "new<br>";'
        '  }'
        '});'
        'return div;')
    self._driver.TouchFlick(div, dx, dy, speed)
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'br')))

  def testTouchPinch(self):
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/touch_action_tests.html'))
    width_before_pinch = self._driver.ExecuteScript('return window.innerWidth;')
    height_before_pinch = self._driver.ExecuteScript(
        'return window.innerHeight;')
    self._driver.TouchPinch(width_before_pinch / 2,
                            height_before_pinch / 2,
                            2.0)
    width_after_pinch = self._driver.ExecuteScript('return window.innerWidth;')
    self.assertAlmostEqual(2.0, float(width_before_pinch) / width_after_pinch)

  def testBrowserDoesntSupportSyntheticGestures(self):
    # WebView on KitKat does not support synthetic gesture commands in DevTools,
    # so touch action tests have been disabled for chromedriver_webview_shell.
    # TODO(samuong): when this test starts failing, re-enable touch tests and
    # delete this test.
    if _ANDROID_PACKAGE_KEY:
      if _ANDROID_PACKAGE_KEY == 'chromedriver_webview_shell':
        self.assertFalse(self._driver.capabilities['hasTouchScreen'])

  def testHasTouchScreen(self):
    self.assertIn('hasTouchScreen', self._driver.capabilities)
    if _ANDROID_PACKAGE_KEY:
      self.assertTrue(self._driver.capabilities['hasTouchScreen'])
    else:
      self.assertFalse(self._driver.capabilities['hasTouchScreen'])

  def testSwitchesToTopFrameAfterNavigation(self):
    self._driver.Load('about:blank')
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/outer.html'))
    frame = self._driver.FindElement('tag name', 'iframe')
    self._driver.SwitchToFrame(frame)
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/outer.html'))
    p = self._driver.FindElement('tag name', 'p')
    self.assertEquals('Two', p.GetText())

  def testSwitchesToTopFrameAfterRefresh(self):
    self._driver.Load('about:blank')
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/outer.html'))
    frame = self._driver.FindElement('tag name', 'iframe')
    self._driver.SwitchToFrame(frame)
    self._driver.Refresh()
    p = self._driver.FindElement('tag name', 'p')
    self.assertEquals('Two', p.GetText())

  def testSwitchesToTopFrameAfterGoingBack(self):
    self._driver.Load('about:blank')
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/outer.html'))
    frame = self._driver.FindElement('tag name', 'iframe')
    self._driver.SwitchToFrame(frame)
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/inner.html'))
    self._driver.GoBack()
    p = self._driver.FindElement('tag name', 'p')
    self.assertEquals('Two', p.GetText())

  def testCanSwitchToPrintPreviewDialog(self):
    old_handles = self._driver.GetWindowHandles()
    self.assertEquals(1, len(old_handles))
    self._driver.ExecuteScript('setTimeout(function(){window.print();}, 0);')
    new_window_handle = self.WaitForNewWindow(self._driver, old_handles)
    self.assertNotEqual(None, new_window_handle)
    self._driver.SwitchToWindow(new_window_handle)
    self.assertEquals('chrome://print/', self._driver.GetCurrentUrl())

  def testCanClickInIframes(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/nested.html'))
    a = self._driver.FindElement('tag name', 'a')
    a.Click()
    frame_url = self._driver.ExecuteScript('return window.location.href')
    self.assertTrue(frame_url.endswith('#one'))
    frame = self._driver.FindElement('tag name', 'iframe')
    self._driver.SwitchToFrame(frame)
    a = self._driver.FindElement('tag name', 'a')
    a.Click()
    frame_url = self._driver.ExecuteScript('return window.location.href')
    self.assertTrue(frame_url.endswith('#two'))

  def testDoesntHangOnFragmentNavigation(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html'))
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/empty.html#x'))

  def SetCookie(self, request):
    return {'Set-Cookie': 'x=y; HttpOnly'}, "<!DOCTYPE html><html></html>"

  def testGetHttpOnlyCookie(self):
    self._http_server.SetCallbackForPath('/setCookie', self.SetCookie)
    self._driver.Load(self.GetHttpUrlForFile('/setCookie'))
    self._driver.AddCookie({'name': 'a', 'value': 'b'})
    cookies = self._driver.GetCookies()
    self.assertEquals(2, len(cookies))
    for cookie in cookies:
      self.assertIn('name', cookie)
      if cookie['name'] == 'a':
        self.assertFalse(cookie['httpOnly'])
      elif cookie['name'] == 'x':
        self.assertTrue(cookie['httpOnly'])
      else:
        self.fail('unexpected cookie: %s' % json.dumps(cookie))

  def testGetUrlOnInvalidUrl(self):
    # Make sure we don't return 'data:text/html,chromewebdata' (see
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1272). RFC 6761
    # requires domain registrars to keep 'invalid.' unregistered (see
    # https://tools.ietf.org/html/rfc6761#section-6.4).
    self._driver.Load('http://invalid./')
    self.assertEquals('http://invalid./', self._driver.GetCurrentUrl())

  def testCanClickAlertInIframes(self):
    # This test requires that the page be loaded from a file:// URI, rather than
    # the test HTTP server.
    path = os.path.join(chrome_paths.GetTestData(), 'chromedriver',
      'page_with_frame.html')
    url = 'file://' + urllib.pathname2url(path)
    self._driver.Load(url)
    frame = self._driver.FindElement('id', 'frm')
    self._driver.SwitchToFrame(frame)
    a = self._driver.FindElement('id', 'btn')
    a.Click()
    self.WaitForCondition(lambda: self._driver.IsAlertOpen())
    self._driver.HandleAlert(True)

  def testThrowErrorWithExecuteScript(self):
    self.assertRaisesRegexp(
        chromedriver.UnknownError, "some error",
        self._driver.ExecuteScript, 'throw new Error("some error")')

  def testDoesntCrashWhenScriptLogsUndefinedValue(self):
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1547
    self._driver.ExecuteScript('var b; console.log(b);')

  def testDoesntThrowWhenPageLogsUndefinedValue(self):
    # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1547
    self._driver.Load(self.GetHttpUrlForFile(
        '/chromedriver/log_undefined_value.html'))

  def testCanSetCheckboxWithSpaceKey(self):
     self._driver.Load('about:blank')
     self._driver.ExecuteScript(
         "document.body.innerHTML = '<input type=\"checkbox\">';")
     checkbox = self._driver.FindElement('tag name', 'input')
     self.assertFalse(
         self._driver.ExecuteScript('return arguments[0].checked', checkbox))
     checkbox.SendKeys(' ')
     self.assertTrue(
         self._driver.ExecuteScript('return arguments[0].checked', checkbox))

  def testElementReference(self):
    self._driver.Load(self.GetHttpUrlForFile('/chromedriver/element_ref.html'))
    element = self._driver.FindElement('id', 'link')
    self._driver.FindElements('tag name', 'br')
    w3c_id_length = 36
    if (self._driver.w3c_compliant):
      self.assertEquals(len(element._id), w3c_id_length)

  def testFindElementWhenElementIsOverridden(self):
    self._driver.Load('about:blank')
    self._driver.ExecuteScript(
        'document.body.appendChild(document.createElement("a"));')
    self._driver.ExecuteScript('window.Element = {}')
    self.assertEquals(1, len(self._driver.FindElements('tag name', 'a')))

  def testExecuteScriptWhenObjectPrototypeIsModified(self):
    # Some JavaScript libraries (e.g. MooTools) do things like this. For context
    # see https://bugs.chromium.org/p/chromedriver/issues/detail?id=1521
    self._driver.Load('about:blank')
    self._driver.ExecuteScript('Object.prototype.$family = undefined;')
    self.assertEquals(1, self._driver.ExecuteScript('return 1;'))


class ChromeDriverPageLoadTimeoutTest(ChromeDriverBaseTestWithWebServer):

  class _RequestHandler(object):
    def __init__(self):
      self.request_received_event = threading.Event()
      self.send_response_event = threading.Event()

    def handle(self, request):
      self.request_received_event.set()
      # Don't hang infinitely, 10 seconds are enough.
      self.send_response_event.wait(10)
      self.send_response_event.clear()
      return {'Cache-Control': 'no-store'}, 'Hi!'

  def setUp(self):
    self._handler = ChromeDriverPageLoadTimeoutTest._RequestHandler()
    self._http_server.SetCallbackForPath('/hang', self._handler.handle)
    super(ChromeDriverPageLoadTimeoutTest, self).setUp()

    self._driver = self.CreateDriver(
        chrome_switches=['host-resolver-rules=MAP * 127.0.0.1'])
    self._initial_url = self.GetHttpUrlForFile('/chromedriver/empty.html')
    self._driver.Load(self._initial_url)
    # NB: With a too small timeout chromedriver might not send the
    # Navigate command at all.
    self._driver.SetTimeout('page load', 500) # 500 ms

  def tearDown(self):
    super(ChromeDriverPageLoadTimeoutTest, self).tearDown()
    self._http_server.SetCallbackForPath('/hang', None)

  def _LoadHangingUrl(self, host=None):
    self._driver.Load(self._http_server.GetUrl(host) + '/hang')

  def _CheckPageLoadTimeout(self, action):
    self._handler.request_received_event.clear()
    timed_out = False
    try:
      action()
    except chromedriver.ChromeDriverException as e:
      self.assertNotEqual(-1, e.message.find('timeout'))
      timed_out = True
    finally:
      self._handler.send_response_event.set()

    self.assertTrue(timed_out)
    # Verify that the browser actually made that request.
    self.assertTrue(self._handler.request_received_event.wait(1))

  def testPageLoadTimeout(self):
    self._CheckPageLoadTimeout(self._LoadHangingUrl)
    self.assertEquals(self._initial_url, self._driver.GetCurrentUrl())

  def testPageLoadTimeoutCrossDomain(self):
    # Cross-domain navigation is likely to be a cross-process one. In this case
    # DevToolsAgentHost behaves quite differently and does not send command
    # responses if the navigation hangs, so this case deserves a dedicated test.
    self._CheckPageLoadTimeout(lambda: self._LoadHangingUrl('foo.bar'))
    self.assertEquals(self._initial_url, self._driver.GetCurrentUrl())

  def testHistoryNavigationWithPageLoadTimeout(self):
    # Allow the page to load for the first time.
    self._handler.send_response_event.set()
    self._LoadHangingUrl()
    self.assertTrue(self._handler.request_received_event.wait(1))

    self._driver.GoBack()
    self._CheckPageLoadTimeout(self._driver.GoForward)
    self.assertEquals(self._initial_url, self._driver.GetCurrentUrl())

  def testRefreshWithPageLoadTimeout(self):
    # Allow the page to load for the first time.
    self._handler.send_response_event.set()
    self._LoadHangingUrl()
    self.assertTrue(self._handler.request_received_event.wait(1))

    self._CheckPageLoadTimeout(self._driver.Refresh)


class ChromeDriverAndroidTest(ChromeDriverBaseTest):
  """End to end tests for Android-specific tests."""

  def testLatestAndroidAppInstalled(self):
    if ('stable' not in _ANDROID_PACKAGE_KEY and
        'beta' not in _ANDROID_PACKAGE_KEY):
      return

    self._driver = self.CreateDriver()

    try:
      omaha_list = json.loads(
          urllib2.urlopen('http://omahaproxy.appspot.com/all.json').read())
      for l in omaha_list:
        if l['os'] != 'android':
          continue
        for v in l['versions']:
          if (('stable' in v['channel'] and 'stable' in _ANDROID_PACKAGE_KEY) or
              ('beta' in v['channel'] and 'beta' in _ANDROID_PACKAGE_KEY)):
            omaha = map(int, v['version'].split('.'))
            device = map(int, self._driver.capabilities['version'].split('.'))
            self.assertTrue(omaha <= device)
            return
      raise RuntimeError('Malformed omaha JSON')
    except urllib2.URLError as e:
      print 'Unable to fetch current version info from omahaproxy (%s)' % e

  def testDeviceManagement(self):
    self._drivers = [self.CreateDriver()
                     for _ in device_utils.DeviceUtils.HealthyDevices()]
    self.assertRaises(chromedriver.UnknownError, self.CreateDriver)
    self._drivers[0].Quit()
    self._drivers[0] = self.CreateDriver()

  def testScreenOrientation(self):
    self._driver = self.CreateDriver()
    self._driver.Load(
      ChromeDriverTest.GetHttpUrlForFile('/chromedriver/orientation_test.html'))
    screen_orientation_js = self._driver.ExecuteScript(
        'return screen.orientation.type')
    screen_orientation = self._driver.GetScreenOrientation()['orientation']
    if screen_orientation == "LANDSCAPE":
      screen_orientation = 'landscape-primary'
    elif screen_orientation == "PORTRAIT":
      screen_orientation = 'portrait-primary'
    self.assertEqual(screen_orientation, screen_orientation_js)

    self._driver.SetScreenOrientation("portrait-primary")
    screen_orientation = self._driver.GetScreenOrientation()
    self.WaitForCondition(
      lambda: 'orientation change 1' in self._driver.FindElement(
        'tag name', 'div').GetText())
    self.assertEqual(screen_orientation['orientation'], "PORTRAIT")

    self._driver.SetScreenOrientation("portrait-secondary")
    self.WaitForCondition(
      lambda: 'orientation change 2' in self._driver.FindElement(
        'tag name', 'div').GetText())
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "PORTRAIT")

    self._driver.SetScreenOrientation("PORTRAIT")
    self.WaitForCondition(
      lambda: 'orientation change 3' in self._driver.FindElement(
        'tag name', 'div').GetText())
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "PORTRAIT")

    self._driver.SetScreenOrientation("landscape-primary")
    self.WaitForCondition(
      lambda: 'orientation change 4' in self._driver.FindElement(
        'tag name', 'div').GetText())
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "LANDSCAPE")

    self._driver.SetScreenOrientation("landscape-secondary")
    self.WaitForCondition(
      lambda: 'orientation change 5' in self._driver.FindElement(
        'tag name', 'div').GetText())
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "LANDSCAPE")

    self._driver.SetScreenOrientation("LANDSCAPE")
    self.WaitForCondition(
      lambda: 'orientation change 6' in self._driver.FindElement(
        'tag name', 'div').GetText())
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "LANDSCAPE")

  def testMultipleScreenOrientationChanges(self):
    self._driver = self.CreateDriver()

    self._driver.SetScreenOrientation('PORTRAIT')
    self.assertEqual(
      self._driver.GetScreenOrientation()['orientation'], 'PORTRAIT')

    self._driver.SetScreenOrientation('PORTRAIT')
    self.assertEqual(
      self._driver.GetScreenOrientation()['orientation'], 'PORTRAIT')

    self._driver.DeleteScreenOrientation()
    self._driver.DeleteScreenOrientation()

  def testDeleteScreenOrientationManual(self):
    self._driver = self.CreateDriver()

    manual_test = False;

    self._driver.SetScreenOrientation("LANDSCAPE")
    screen_orientation = self._driver.GetScreenOrientation()
    self.assertEqual(screen_orientation['orientation'], "LANDSCAPE")
    if(manual_test):
      time.sleep(10)
      # While sleeping, test that the orientation cannot be changed.

    print "Screen orientation lock deleted."
    self._driver.DeleteScreenOrientation();
    if(manual_test):
      time.sleep(10)
      # While sleeping, test that orientation can be changed by manually
      # rotating the device.

  def testScreenOrientationAcrossMultipleTabs(self):
    self._driver = self.CreateDriver()

    self._driver.SetScreenOrientation('LANDSCAPE')
    self._driver.Load(
      ChromeDriverTest.GetHttpUrlForFile('/chromedriver/page_test.html'))
    window1 = self._driver.GetCurrentWindowHandle()
    self._driver.FindElement('id', 'link').Click()
    orientation = self._driver.GetScreenOrientation()
    self.assertEqual(orientation['orientation'], 'LANDSCAPE')

    self._driver.ExecuteScript('window.name = "oldWindow";')
    self._driver.SwitchToWindow('oldWindow')
    self.assertEqual(window1, self._driver.GetCurrentWindowHandle())
    orientation = self._driver.GetScreenOrientation()
    self.assertEqual(orientation['orientation'], 'LANDSCAPE')


class ChromeDownloadDirTest(ChromeDriverBaseTest):

  def __init__(self, *args, **kwargs):
    super(ChromeDownloadDirTest, self).__init__(*args, **kwargs)
    self._temp_dirs = []

  def CreateTempDir(self):
    temp_dir = tempfile.mkdtemp()
    self._temp_dirs.append(temp_dir)
    return temp_dir

  def RespondWithCsvFile(self, request):
    return {'Content-Type': 'text/csv'}, 'a,b,c\n1,2,3\n'

  def WaitForFileToDownload(self, path):
    deadline = time.time() + 60
    while True:
      time.sleep(0.1)
      if os.path.isfile(path) or time.time() > deadline:
        break
    self.assertTrue(os.path.isfile(path), "Failed to download file!")

  def tearDown(self):
    # Call the superclass tearDown() method before deleting temp dirs, so that
    # Chrome has a chance to exit before its user data dir is blown away from
    # underneath it.
    super(ChromeDownloadDirTest, self).tearDown()
    for temp_dir in self._temp_dirs:
      shutil.rmtree(temp_dir)

  def testFileDownloadWithClick(self):
    download_dir = self.CreateTempDir()
    download_name = os.path.join(download_dir, 'a_red_dot.png')
    driver = self.CreateDriver(download_dir=download_dir)
    driver.Load(ChromeDriverTest.GetHttpUrlForFile(
        '/chromedriver/download.html'))
    driver.FindElement('id', 'red-dot').Click()
    self.WaitForFileToDownload(download_name)
    self.assertEqual(
        ChromeDriverTest.GetHttpUrlForFile('/chromedriver/download.html'),
        driver.GetCurrentUrl())

  def testFileDownloadWithGet(self):
    ChromeDriverTest._http_server.SetCallbackForPath(
        '/abc.csv', self.RespondWithCsvFile)
    download_dir = self.CreateTempDir()
    download_name = os.path.join(download_dir, 'abc.csv')
    driver = self.CreateDriver(download_dir=download_dir)
    original_url = driver.GetCurrentUrl()
    driver.Load(ChromeDriverTest.GetHttpUrlForFile('/abc.csv'))
    self.WaitForFileToDownload(os.path.join(download_dir, 'abc.csv'))
    major_version = int(driver.capabilities['version'].split('.')[0])
    if major_version > 43:
      # For some reason, the URL in M43 changes from 'data:,' to '', so we
      # need to avoid doing this assertion unless we're on M44+.
      # TODO(samuong): Assert unconditionally once we stop supporting M43.
      self.assertEqual(original_url, driver.GetCurrentUrl())

  def testDownloadDirectoryOverridesExistingPreferences(self):
    user_data_dir = self.CreateTempDir()
    download_dir = self.CreateTempDir()
    sub_dir = os.path.join(user_data_dir, 'Default')
    os.mkdir(sub_dir)
    prefs_file_path = os.path.join(sub_dir, 'Preferences')

    prefs = {
      'test': 'this should not be changed',
      'download': {
        'default_directory': '/old/download/directory'
      }
    }

    with open(prefs_file_path, 'w') as f:
      json.dump(prefs, f)

    driver = self.CreateDriver(
        chrome_switches=['user-data-dir=' + user_data_dir],
        download_dir=download_dir)

    with open(prefs_file_path) as f:
      prefs = json.load(f)

    self.assertEqual('this should not be changed', prefs['test'])
    download = prefs['download']
    self.assertEqual(download['default_directory'], download_dir)


class ChromeSwitchesCapabilityTest(ChromeDriverBaseTest):
  """Tests that chromedriver properly processes chromeOptions.args capabilities.

  Makes sure the switches are passed to Chrome.
  """

  def testSwitchWithoutArgument(self):
    """Tests that switch --dom-automation can be passed to Chrome.

    Unless --dom-automation is specified, window.domAutomationController
    is undefined.
    """
    driver = self.CreateDriver(chrome_switches=['dom-automation'])
    self.assertNotEqual(
        None,
        driver.ExecuteScript('return window.domAutomationController'))


class ChromeDesiredCapabilityTest(ChromeDriverBaseTest):
  """Tests that chromedriver properly processes desired capabilities."""

  def testUnexpectedAlertBehaviour(self):
    driver = self.CreateDriver(unexpected_alert_behaviour="accept")
    self.assertEquals("accept",
                      driver.capabilities['unexpectedAlertBehaviour'])
    driver.ExecuteScript('alert("HI");')
    self.WaitForCondition(driver.IsAlertOpen)
    self.assertRaisesRegexp(chromedriver.UnexpectedAlertOpen,
                            'unexpected alert open: {Alert text : HI}',
                            driver.FindElement, 'tag name', 'div')
    self.assertFalse(driver.IsAlertOpen())


class ChromeExtensionsCapabilityTest(ChromeDriverBaseTest):
  """Tests that chromedriver properly processes chromeOptions.extensions."""

  def _PackExtension(self, ext_path):
    return base64.b64encode(open(ext_path, 'rb').read())

  def testExtensionsInstall(self):
    """Checks that chromedriver can take the extensions in crx format."""
    crx_1 = os.path.join(_TEST_DATA_DIR, 'ext_test_1.crx')
    crx_2 = os.path.join(_TEST_DATA_DIR, 'ext_test_2.crx')
    self.CreateDriver(chrome_extensions=[self._PackExtension(crx_1),
                                         self._PackExtension(crx_2)])

  def testExtensionsInstallZip(self):
    """Checks that chromedriver can take the extensions in zip format."""
    zip_1 = os.path.join(_TEST_DATA_DIR, 'ext_test_1.zip')
    self.CreateDriver(chrome_extensions=[self._PackExtension(zip_1)])

  def testWaitsForExtensionToLoad(self):
    did_load_event = threading.Event()
    server = webserver.SyncWebServer()
    def RunServer():
      time.sleep(5)
      server.RespondWithContent('<html>iframe</html>')
      did_load_event.set()

    thread = threading.Thread(target=RunServer)
    thread.daemon = True
    thread.start()
    crx = os.path.join(_TEST_DATA_DIR, 'ext_slow_loader.crx')
    driver = self.CreateDriver(
        chrome_switches=['user-agent=' + server.GetUrl()],
        chrome_extensions=[self._PackExtension(crx)])
    self.assertTrue(did_load_event.is_set())

  def testCanLaunchApp(self):
    app_path = os.path.join(_TEST_DATA_DIR, 'test_app')
    driver = self.CreateDriver(chrome_switches=['load-extension=%s' % app_path])
    old_handles = driver.GetWindowHandles()
    self.assertEqual(1, len(old_handles))
    driver.LaunchApp('gegjcdcfeiojglhifpmibkadodekakpc')
    new_window_handle = self.WaitForNewWindow(driver, old_handles)
    driver.SwitchToWindow(new_window_handle)
    body_element = driver.FindElement('tag name', 'body')
    self.assertEqual('It works!', body_element.GetText())

  def testCanInspectBackgroundPage(self):
    app_path = os.path.join(_TEST_DATA_DIR, 'test_app')
    extension_path = os.path.join(_TEST_DATA_DIR, 'all_frames')
    driver = self.CreateDriver(
        chrome_switches=['load-extension=%s' % app_path],
        experimental_options={'windowTypes': ['background_page']})
    old_handles = driver.GetWindowHandles()
    driver.LaunchApp('gegjcdcfeiojglhifpmibkadodekakpc')
    new_window_handle = self.WaitForNewWindow(
        driver, old_handles, check_closed_windows=False)
    handles = driver.GetWindowHandles()
    for handle in handles:
      driver.SwitchToWindow(handle)
      if driver.GetCurrentUrl() == 'chrome-extension://' \
          'gegjcdcfeiojglhifpmibkadodekakpc/_generated_background_page.html':
        self.assertEqual(42, driver.ExecuteScript('return magic;'))
        return
    self.fail("couldn't find generated background page for test app")

  def testIFrameWithExtensionsSource(self):
    crx_path = os.path.join(_TEST_DATA_DIR, 'frames_extension.crx')
    driver = self.CreateDriver(
        chrome_extensions=[self._PackExtension(crx_path)])
    driver.Load(
        ChromeDriverTest._http_server.GetUrl() +
          '/chromedriver/iframe_extension.html')
    driver.SwitchToFrame('testframe')
    element = driver.FindElement('id', 'p1')
    self.assertEqual('Its a frame with extension source', element.GetText())

  def testDontExecuteScriptsInContentScriptContext(self):
    # This test extension has a content script which runs in all frames (see
    # https://developer.chrome.com/extensions/content_scripts) which causes each
    # frame on the page to be associated with multiple JS execution contexts.
    # Make sure that ExecuteScript operates on the page's context, rather than
    # the extension's content script's one.
    extension_path = os.path.join(_TEST_DATA_DIR, 'all_frames')
    driver = self.CreateDriver(
        chrome_switches=['load-extension=%s' % extension_path])
    driver.Load(
        ChromeDriverTest._http_server.GetUrl() + '/chromedriver/container.html')
    driver.SwitchToMainFrame()
    self.assertEqual('one', driver.ExecuteScript("return window['global_var']"))
    driver.SwitchToFrame('iframe')
    self.assertEqual('two', driver.ExecuteScript("return window['iframe_var']"))

  def testDontUseAutomationExtension(self):
    driver = self.CreateDriver(
        experimental_options={'useAutomationExtension': False})
    driver.Load('chrome:version')
    command_line = driver.FindElement('id', 'command_line').GetText()
    self.assertNotIn('load-extension', command_line)


class ChromeLogPathCapabilityTest(ChromeDriverBaseTest):
  """Tests that chromedriver properly processes chromeOptions.logPath."""

  LOG_MESSAGE = 'Welcome to ChromeLogPathCapabilityTest!'

  def testChromeLogPath(self):
    """Checks that user can specify the path of the chrome log.

    Verifies that a log message is written into the specified log file.
    """
    tmp_log_path = tempfile.NamedTemporaryFile()
    driver = self.CreateDriver(chrome_log_path=tmp_log_path.name)
    driver.ExecuteScript('console.info("%s")' % self.LOG_MESSAGE)
    driver.Quit()
    self.assertTrue(self.LOG_MESSAGE in open(tmp_log_path.name).read())


class MobileEmulationCapabilityTest(ChromeDriverBaseTest):
  """Tests that ChromeDriver processes chromeOptions.mobileEmulation.

  Makes sure the device metrics are overridden in DevTools and user agent is
  overridden in Chrome.
  """

  @staticmethod
  def GlobalSetUp():
    def respondWithUserAgentString(request):
      return {}, """
        <html>
        <body>%s</body>
        </html>""" % request.GetHeader('User-Agent')

    def respondWithUserAgentStringUseDeviceWidth(request):
      return {}, """
        <html>
        <head>
        <meta name="viewport" content="width=device-width,minimum-scale=1.0">
        </head>
        <body>%s</body>
        </html>""" % request.GetHeader('User-Agent')

    MobileEmulationCapabilityTest._http_server = webserver.WebServer(
        chrome_paths.GetTestData())
    MobileEmulationCapabilityTest._http_server.SetCallbackForPath(
        '/userAgent', respondWithUserAgentString)
    MobileEmulationCapabilityTest._http_server.SetCallbackForPath(
        '/userAgentUseDeviceWidth', respondWithUserAgentStringUseDeviceWidth)

  @staticmethod
  def GlobalTearDown():
    MobileEmulationCapabilityTest._http_server.Shutdown()

  def testDeviceMetricsWithStandardWidth(self):
    driver = self.CreateDriver(
        mobile_emulation = {
            'deviceMetrics': {'width': 360, 'height': 640, 'pixelRatio': 3},
            'userAgent': 'Mozilla/5.0 (Linux; Android 4.2.1; en-us; Nexus 5 Bui'
                         'ld/JOP40D) AppleWebKit/535.19 (KHTML, like Gecko) Chr'
                         'ome/18.0.1025.166 Mobile Safari/535.19'
            })
    driver.SetWindowSize(600, 400)
    driver.Load(self._http_server.GetUrl() + '/userAgent')
    self.assertTrue(driver.capabilities['mobileEmulationEnabled'])
    self.assertEqual(360, driver.ExecuteScript('return window.screen.width'))
    self.assertEqual(640, driver.ExecuteScript('return window.screen.height'))

  def testDeviceMetricsWithDeviceWidth(self):
    driver = self.CreateDriver(
        mobile_emulation = {
            'deviceMetrics': {'width': 360, 'height': 640, 'pixelRatio': 3},
            'userAgent': 'Mozilla/5.0 (Linux; Android 4.2.1; en-us; Nexus 5 Bui'
                         'ld/JOP40D) AppleWebKit/535.19 (KHTML, like Gecko) Chr'
                         'ome/18.0.1025.166 Mobile Safari/535.19'
            })
    driver.Load(self._http_server.GetUrl() + '/userAgentUseDeviceWidth')
    self.assertTrue(driver.capabilities['mobileEmulationEnabled'])
    self.assertEqual(360, driver.ExecuteScript('return window.screen.width'))
    self.assertEqual(640, driver.ExecuteScript('return window.screen.height'))

  def testUserAgent(self):
    driver = self.CreateDriver(
        mobile_emulation = {'userAgent': 'Agent Smith'})
    driver.Load(self._http_server.GetUrl() + '/userAgent')
    body_tag = driver.FindElement('tag name', 'body')
    self.assertEqual("Agent Smith", body_tag.GetText())

  def testDeviceName(self):
    driver = self.CreateDriver(
        mobile_emulation = {'deviceName': 'Google Nexus 5'})
    driver.Load(self._http_server.GetUrl() + '/userAgentUseDeviceWidth')
    self.assertEqual(360, driver.ExecuteScript('return window.screen.width'))
    self.assertEqual(640, driver.ExecuteScript('return window.screen.height'))
    body_tag = driver.FindElement('tag name', 'body')
    self.assertEqual(
        'Mozilla/5.0 (Linux; Android 4.4.4; Nexus 5 Build/KTU84P) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/38.0.2125.114 Mobile '
        'Safari/537.36',
        body_tag.GetText())

  def testSendKeysToElement(self):
    driver = self.CreateDriver(
        mobile_emulation = {'deviceName': 'Google Nexus 5'})
    text = driver.ExecuteScript(
        'document.body.innerHTML = \'<input type="text">\';'
        'var input = document.getElementsByTagName("input")[0];'
        'input.addEventListener("change", function() {'
        '  document.body.appendChild(document.createElement("br"));'
        '});'
        'return input;')
    text.SendKeys('0123456789+-*/ Hi')
    text.SendKeys(', there!')
    value = driver.ExecuteScript('return arguments[0].value;', text)
    self.assertEquals('0123456789+-*/ Hi, there!', value)

  def testClickElement(self):
    driver = self.CreateDriver(
        mobile_emulation = {'deviceName': 'Google Nexus 5'})
    driver.Load('about:blank')
    div = driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.addEventListener("click", function() {'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    div.Click()
    self.assertEquals(1, len(driver.FindElements('tag name', 'br')))

  def testTapElement(self):
    driver = self.CreateDriver(
        mobile_emulation = {'deviceName': 'Google Nexus 5'})
    driver.Load('about:blank')
    div = driver.ExecuteScript(
        'document.body.innerHTML = "<div>old</div>";'
        'var div = document.getElementsByTagName("div")[0];'
        'div.addEventListener("touchstart", function() {'
        '  div.innerHTML="new<br>";'
        '});'
        'return div;')
    div.SingleTap()
    self.assertEquals(1, len(driver.FindElements('tag name', 'br')))

  def testHasTouchScreen(self):
    driver = self.CreateDriver(
        mobile_emulation = {'deviceName': 'Google Nexus 5'})
    self.assertIn('hasTouchScreen', driver.capabilities)
    self.assertTrue(driver.capabilities['hasTouchScreen'])

  def testDoesntWaitWhenPageLoadStrategyIsNone(self):
    class HandleRequest(object):
      def __init__(self):
        self.sent_hello = threading.Event()

      def slowPage(self, request):
        self.sent_hello.wait(2)
        return {}, """
        <html>
        <body>hello</body>
        </html>"""

    handler = HandleRequest()
    self._http_server.SetCallbackForPath('/slow', handler.slowPage)

    driver = self.CreateDriver(page_load_strategy='none')
    self.assertEquals('none', driver.capabilities['pageLoadStrategy'])

    driver.Load(self._http_server.GetUrl() + '/chromedriver/empty.html')
    driver.Load(self._http_server.GetUrl() + '/slow')
    self.assertFalse('hello' in driver.GetPageSource())
    handler.sent_hello.set()
    self.WaitForCondition(lambda: 'hello' in driver.GetPageSource())
    self.assertTrue('hello' in driver.GetPageSource())

  def testUnsupportedPageLoadStrategyRaisesException(self):
    self.assertRaises(chromedriver.UnknownError,
                      self.CreateDriver, page_load_strategy="unsupported")

  def testNetworkConnectionDisabledByDefault(self):
    driver = self.CreateDriver()
    self.assertFalse(driver.capabilities['networkConnectionEnabled'])

  def testNetworkConnectionUnsupported(self):
    driver = self.CreateDriver()
    # Network connection capability must be enabled to set/retrieve
    self.assertRaises(chromedriver.UnknownError,
                      driver.GetNetworkConnection)

    self.assertRaises(chromedriver.UnknownError,
                      driver.SetNetworkConnection, 0x1)

  def testNetworkConnectionEnabled(self):
    # mobileEmulation must be enabled for networkConnection to be enabled
    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)
    self.assertTrue(driver.capabilities['mobileEmulationEnabled'])
    self.assertTrue(driver.capabilities['networkConnectionEnabled'])

  def testEmulateNetworkConnection4g(self):
    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)
    # Test 4G connection.
    connection_type = 0x8
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)
    network = driver.GetNetworkConnection()
    self.assertEquals(network, connection_type)

  def testEmulateNetworkConnectionMultipleBits(self):
    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)
    # Connection with 4G, 3G, and 2G bits on.
    # Tests that 4G takes precedence.
    connection_type = 0x38
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)
    network = driver.GetNetworkConnection()
    self.assertEquals(network, connection_type)

  def testWifiAndAirplaneModeEmulation(self):
    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)
    # Connection with both Wifi and Airplane Mode on.
    # Tests that Wifi takes precedence over Airplane Mode.
    connection_type = 0x3
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)
    network = driver.GetNetworkConnection()
    self.assertEquals(network, connection_type)

  def testNetworkConnectionTypeIsAppliedToAllTabsImmediately(self):
    def respondWithString(request):
      return {}, """
        <html>
        <body>%s</body>
        </html>""" % "hello world!"

    self._http_server.SetCallbackForPath(
      '/helloworld', respondWithString)

    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)

    # Set network to online
    connection_type = 0x10
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)

    # Open a window with two divs counting successful + unsuccessful
    # attempts to complete XML task
    driver.Load(
        self._http_server.GetUrl() +'/chromedriver/xmlrequest_test.html')
    window1_handle = driver.GetCurrentWindowHandle()
    old_handles = driver.GetWindowHandles()
    driver.FindElement('id', 'requestButton').Click()

    driver.FindElement('id', 'link').Click()
    new_window_handle = self.WaitForNewWindow(driver, old_handles)
    self.assertNotEqual(None, new_window_handle)
    driver.SwitchToWindow(new_window_handle)
    self.assertEquals(new_window_handle, driver.GetCurrentWindowHandle())

    # Set network to offline to determine whether the XML task continues to
    # run in the background, indicating that the conditions are only applied
    # to the current WebView
    connection_type = 0x1
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)

    driver.SwitchToWindow(window1_handle)
    connection_type = 0x1

  def testNetworkConnectionTypeIsAppliedToAllTabs(self):
    driver = self.CreateDriver(
        mobile_emulation={'deviceName': 'Google Nexus 5'},
        network_connection=True)
    driver.Load(self._http_server.GetUrl() +'/chromedriver/page_test.html')
    window1_handle = driver.GetCurrentWindowHandle()
    old_handles = driver.GetWindowHandles()

    # Test connection is offline.
    connection_type = 0x1;
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)
    network = driver.GetNetworkConnection()
    self.assertEquals(network, connection_type)

    # Navigate to another window.
    driver.FindElement('id', 'link').Click()
    new_window_handle = self.WaitForNewWindow(driver, old_handles)
    self.assertNotEqual(None, new_window_handle)
    driver.SwitchToWindow(new_window_handle)
    self.assertEquals(new_window_handle, driver.GetCurrentWindowHandle())
    self.assertRaises(
        chromedriver.NoSuchElement, driver.FindElement, 'id', 'link')

    # Set connection to 3G in second window.
    connection_type = 0x10;
    returned_type = driver.SetNetworkConnection(connection_type)
    self.assertEquals(connection_type, returned_type)

    driver.SwitchToWindow(window1_handle)
    self.assertEquals(window1_handle, driver.GetCurrentWindowHandle())

    # Test whether first window has old or new network conditions.
    network = driver.GetNetworkConnection()
    self.assertEquals(network, connection_type)

  def testW3cCompliantResponses(self):
    # It's an error to send W3C format request without W3C capability flag.
    with self.assertRaises(chromedriver.SessionNotCreatedException):
      self.CreateDriver(send_w3c_request=True)

    # W3C capability flag is ignored in a legacy format request.
    driver = self.CreateDriver(send_w3c_capability=True)
    self.assertFalse(driver.w3c_compliant)

    # W3C compliant responses should only be received when the capability has
    # been set and the request was sent in the correct format.
    driver = self.CreateDriver(send_w3c_capability=True, send_w3c_request=True)
    self.assertTrue(driver.w3c_compliant)

    # Asserts that errors are being raised correctly in the test client
    # with a W3C compliant driver.
    self.assertRaises(chromedriver.UnknownError,
                      driver.GetNetworkConnection)

  def testNonCompliantByDefault(self):
    driver = self.CreateDriver();
    self.assertFalse(driver.w3c_compliant)


class ChromeDriverLogTest(ChromeDriverBaseTest):
  """Tests that chromedriver produces the expected log file."""

  UNEXPECTED_CHROMEOPTION_CAP = 'unexpected_chromeoption_capability'
  LOG_MESSAGE = 'unrecognized chrome option: %s' % UNEXPECTED_CHROMEOPTION_CAP

  def testChromeDriverLog(self):
    _, tmp_log_path = tempfile.mkstemp(prefix='chromedriver_log_')
    chromedriver_server = server.Server(
        _CHROMEDRIVER_BINARY, log_path=tmp_log_path)
    try:
      driver = chromedriver.ChromeDriver(
          chromedriver_server.GetUrl(), chrome_binary=_CHROME_BINARY,
          experimental_options={ self.UNEXPECTED_CHROMEOPTION_CAP : 1 })
      driver.Quit()
    except chromedriver.ChromeDriverException, e:
      self.assertTrue(self.LOG_MESSAGE in e.message)
    finally:
      chromedriver_server.Kill()
    with open(tmp_log_path, 'r') as f:
      self.assertTrue(self.LOG_MESSAGE in f.read())

  def testDisablingDriverLogsSuppressesChromeDriverLog(self):
    _, tmp_log_path = tempfile.mkstemp(prefix='chromedriver_log_')
    chromedriver_server = server.Server(
        _CHROMEDRIVER_BINARY, log_path=tmp_log_path)
    try:
      driver = self.CreateDriver(
          chromedriver_server.GetUrl(), logging_prefs={'driver':'OFF'})
      driver.Load(
        ChromeDriverTest._http_server.GetUrl() + '/chromedriver/empty.html')
      driver.AddCookie({'name': 'secret_code', 'value': 'bosco'})
      driver.Quit()
    finally:
      chromedriver_server.Kill()
    with open(tmp_log_path, 'r') as f:
      self.assertNotIn('bosco', f.read())


class ChromeLoggingCapabilityTest(ChromeDriverBaseTest):
  """Tests chromedriver tracing support and Inspector event collection."""

  def testPerformanceLogger(self):
    driver = self.CreateDriver(
        experimental_options={'perfLoggingPrefs': {
            'traceCategories': 'blink.console'
          }}, logging_prefs={'performance':'ALL'})
    driver.Load(
        ChromeDriverTest._http_server.GetUrl() + '/chromedriver/empty.html')
    # Mark the timeline; later we will verify the marks appear in the trace.
    driver.ExecuteScript('console.time("foobar")')
    driver.ExecuteScript('console.timeEnd("foobar")')
    logs = driver.GetLog('performance')
    driver.Quit()

    marked_timeline_events = []
    seen_log_domains = {}
    for entry in logs:
      devtools_message = json.loads(entry['message'])['message']
      method = devtools_message['method']
      domain = method[:method.find('.')]
      seen_log_domains[domain] = True
      if method != 'Tracing.dataCollected':
        continue
      self.assertTrue('params' in devtools_message)
      self.assertTrue(isinstance(devtools_message['params'], dict))
      cat = devtools_message['params'].get('cat', '')
      if cat == 'blink.console':
        self.assertTrue(devtools_message['params']['name'] == 'foobar')
        marked_timeline_events.append(devtools_message)
    self.assertEquals(2, len(marked_timeline_events))
    self.assertEquals({'Network', 'Page', 'Tracing'},
                      set(seen_log_domains.keys()))

  def testDevToolsEventsLogger(self):
    """Tests that the correct event type (and no other) is logged"""
    event = 'Page.loadEventFired'
    driver = self.CreateDriver(
        devtools_events_to_log=[event], logging_prefs={'devtools':'ALL'})
    driver.Load('about:blank')
    logs = driver.GetLog('devtools')
    for entry in logs:
      devtools_message = json.loads(entry['message'])
      method = devtools_message['method']
      self.assertTrue('params' in devtools_message)
      self.assertEquals(event, method)

class SessionHandlingTest(ChromeDriverBaseTest):
  """Tests for session operations."""
  def testQuitASessionMoreThanOnce(self):
    driver = self.CreateDriver()
    driver.Quit()
    driver.Quit()

  def testGetSessions(self):
    driver = self.CreateDriver()
    response = driver.GetSessions()
    self.assertEqual(1, len(response))

    driver2 = self.CreateDriver()
    response = driver2.GetSessions()
    self.assertEqual(2, len(response))


class RemoteBrowserTest(ChromeDriverBaseTest):
  """Tests for ChromeDriver remote browser capability."""
  def setUp(self):
    self.assertTrue(_CHROME_BINARY is not None,
                    'must supply a chrome binary arg')

  def testConnectToRemoteBrowser(self):
    port = self.FindFreePort()
    temp_dir = util.MakeTempDir()
    print 'temp dir is ' + temp_dir
    cmd = [_CHROME_BINARY,
           '--remote-debugging-port=%d' % port,
           '--user-data-dir=%s' % temp_dir,
           '--use-mock-keychain']
    if util.IsLinux() and not util.Is64Bit():
      # Workaround for crbug.com/611886.
      cmd.append('--no-sandbox')
      # https://bugs.chromium.org/p/chromedriver/issues/detail?id=1695
      cmd.append('--disable-gpu')
    process = subprocess.Popen(cmd)
    if process is None:
      raise RuntimeError('Chrome could not be started with debugging port')
    try:
      driver = self.CreateDriver(debugger_address='localhost:%d' % port)
      driver.ExecuteScript('console.info("%s")' % 'connecting at %d!' % port)
      driver.Quit()
    finally:
      process.terminate()

  def FindFreePort(self):
    for port in range(10000, 10100):
      try:
        socket.create_connection(('127.0.0.1', port), 0.2).close()
      except socket.error:
        return port
    raise RuntimeError('Cannot find open port')


class PerfTest(ChromeDriverBaseTest):
  """Tests for ChromeDriver perf."""
  def setUp(self):
    self.assertTrue(_REFERENCE_CHROMEDRIVER is not None,
                    'must supply a reference-chromedriver arg')

  def _RunDriverPerfTest(self, name, test_func):
    """Runs a perf test comparing a reference and new ChromeDriver server.

    Args:
      name: The name of the perf test.
      test_func: Called with the server url to perform the test action. Must
                 return the time elapsed.
    """
    class Results(object):
      ref = []
      new = []

    ref_server = server.Server(_REFERENCE_CHROMEDRIVER)
    results = Results()
    result_url_pairs = zip([results.new, results.ref],
                           [_CHROMEDRIVER_SERVER_URL, ref_server.GetUrl()])
    for iteration in range(30):
      for result, url in result_url_pairs:
        result += [test_func(url)]
      # Reverse the order for the next run.
      result_url_pairs = result_url_pairs[::-1]

    def PrintResult(build, result):
      mean = sum(result) / len(result)
      avg_dev = sum([abs(sample - mean) for sample in result]) / len(result)
      print 'perf result', build, name, mean, avg_dev, result
      util.AddBuildStepText('%s %s: %.3f+-%.3f' % (
          build, name, mean, avg_dev))

    # Discard first result, which may be off due to cold start.
    PrintResult('new', results.new[1:])
    PrintResult('ref', results.ref[1:])

  def testSessionStartTime(self):
    def Run(url):
      start = time.time()
      driver = self.CreateDriver(url)
      end = time.time()
      driver.Quit()
      return end - start
    self._RunDriverPerfTest('session start', Run)

  def testSessionStopTime(self):
    def Run(url):
      driver = self.CreateDriver(url)
      start = time.time()
      driver.Quit()
      end = time.time()
      return end - start
    self._RunDriverPerfTest('session stop', Run)

  def testColdExecuteScript(self):
    def Run(url):
      driver = self.CreateDriver(url)
      start = time.time()
      driver.ExecuteScript('return 1')
      end = time.time()
      driver.Quit()
      return end - start
    self._RunDriverPerfTest('cold exe js', Run)

if __name__ == '__main__':
  parser = optparse.OptionParser()
  parser.add_option(
      '', '--chromedriver',
      help='Path to chromedriver server (REQUIRED!)')
  parser.add_option(
      '', '--log-path',
      help='Output verbose server logs to this file')
  parser.add_option(
      '', '--reference-chromedriver',
      help='Path to the reference chromedriver server')
  parser.add_option(
      '', '--chrome', help='Path to a build of the chrome binary')
  parser.add_option(
      '', '--chrome-version', default='HEAD',
      help='Version of chrome. Default is \'HEAD\'.')
  parser.add_option(
      '', '--filter', type='string', default='*',
      help=('Filter for specifying what tests to run, "*" will run all. E.g., '
            '*testStartStop'))
  parser.add_option(
      '', '--android-package',
      help=('Android package key. Possible values: ' +
            str(_ANDROID_NEGATIVE_FILTER.keys())))
  options, args = parser.parse_args()

  options.chromedriver = util.GetAbsolutePathOfUserPath(options.chromedriver)
  if not options.chromedriver or not os.path.exists(options.chromedriver):
    parser.error('chromedriver is required or the given path is invalid.' +
                 'Please run "%s --help" for help' % __file__)

  global _CHROMEDRIVER_BINARY
  _CHROMEDRIVER_BINARY = options.chromedriver

  if (options.android_package and
      options.android_package not in _ANDROID_NEGATIVE_FILTER):
    parser.error('Invalid --android-package')

  chromedriver_server = server.Server(_CHROMEDRIVER_BINARY, options.log_path)
  global _CHROMEDRIVER_SERVER_URL
  _CHROMEDRIVER_SERVER_URL = chromedriver_server.GetUrl()

  global _REFERENCE_CHROMEDRIVER
  _REFERENCE_CHROMEDRIVER = util.GetAbsolutePathOfUserPath(
      options.reference_chromedriver)

  global _CHROME_BINARY
  if options.chrome:
    _CHROME_BINARY = util.GetAbsolutePathOfUserPath(options.chrome)
  else:
    _CHROME_BINARY = None

  global _ANDROID_PACKAGE_KEY
  _ANDROID_PACKAGE_KEY = options.android_package

  if _ANDROID_PACKAGE_KEY:
    devil_chromium.Initialize()

  if options.filter == '*':
    if _ANDROID_PACKAGE_KEY:
      negative_filter = _ANDROID_NEGATIVE_FILTER[_ANDROID_PACKAGE_KEY]
    else:
      negative_filter = _GetDesktopNegativeFilter(options.chrome_version)
    options.filter = '*-' + ':__main__.'.join([''] + negative_filter)

  all_tests_suite = unittest.defaultTestLoader.loadTestsFromModule(
      sys.modules[__name__])
  tests = unittest_util.FilterTestSuite(all_tests_suite, options.filter)
  ChromeDriverTest.GlobalSetUp()
  MobileEmulationCapabilityTest.GlobalSetUp()
  result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(tests)
  ChromeDriverTest.GlobalTearDown()
  MobileEmulationCapabilityTest.GlobalTearDown()
  sys.exit(len(result.failures) + len(result.errors))
