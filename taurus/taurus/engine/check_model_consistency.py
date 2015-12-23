# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2015, Numenta, Inc.  Unless you have purchased from
# Numenta, Inc. a separate commercial license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

"""Perform a consistency check between metrics/models in the mysql taurus.metric
table and the corresponding dynamodb table. Metrics (and their attributes) in
the dynamodb taurs.metric.<environment> table must match the ACTIVE models in
the mysql taurus.metric table. Return non-zero result code if errors are found.
"""

import argparse
import json
import logging
import sys

import boto.dynamodb2.table

from nta.utils.dynamodb_utils import retryOnTransientDynamoDBError
from nta.utils.error_handling import logExceptions

from htmengine.repository.queries import MetricStatus

from taurus.engine import logging_support
from taurus.engine import repository

from taurus.engine.runtime.dynamodb import dynamodb_service
from taurus.engine.runtime.dynamodb.definitions import (
  MetricDynamoDBDefinition)



g_log = logging.getLogger(__name__)



def _parseArgs(args):
  """Parse command-line arguments

  :param list args: The equivalent of `sys.argv[1:]`

  :returns: the args object generated by ``argparse.ArgumentParser.parse_args``
    with the following attributes:
      verbose: True for verbose mode
      warningsAsErrors: True to treat warnings as errors, returning non-zero
        result code for warnings just like for errors.
  """
  parser = argparse.ArgumentParser(description=__doc__)

  parser.add_argument(
    "-v",
    "--verbose",
    action="store_true",
    dest="verbose",
    help=("Turn on verbose mode."))

  parser.add_argument(
    "--warningsAsErrors",
    action="store_true",
    dest="warningsAsErrors",
    help=("Turn on verbose mode."))

  return parser.parse_args(args)



def _getMetricsFromDynamodb(verbose):
  """Retrieve metrics from dynamodb

  :param bool verbose: True for verbose mode

  :returns: sequence of metric records as dicts
  """
  # Connect to DynamoDB and create a proxy of the metric table
  metricTable = boto.dynamodb2.table.Table(
    table_name=MetricDynamoDBDefinition().tableName,
    connection = dynamodb_service.DynamoDBService.connectDynamoDB()
  )

  if verbose:
    g_log.info("Accessing metrics in DynamodDB from %s via %r",
               metricTable.table_name, metricTable.connection)

  resultSet = retryOnTransientDynamoDBError(g_log)(metricTable.scan)()
  return tuple(row for row in resultSet)



def _checkFailedModels(engineMetrics,
                       verbose):  # pylint: disable=W0613
  """Check if any models are in error state

  :param engineMetrics: metric rows from Taurus Engine's respository
  :type engineMetrics: sequence of sqlalchemy.engine.RowProxy

  :param bool verbose: True for verbose mode

  :returns: a two-tuple (warnings, errors), where each of the elements is a
    sequence of two-tuples (caption, details)
  """
  warnings = []

  errorModels = tuple(obj for obj in engineMetrics
                      if obj["status"] == MetricStatus.ERROR)
  if errorModels:
    # It's a warning as far as this check is concerned. If the model failed
    # during creation, we don't expect it to be in dynamodb.
    warnings.append(
      (
        "{} models in ERROR state".format(len(errorModels)),

        "\n".join(
          "WARNING - model in error state: {}".format(
            [str(obj["uid"]), str(obj["name"]), str(obj["message"])])
          for obj in errorModels)
      )
    )

  return warnings, []



def _checkModelParity(engineMetrics,
                      dynamodbMetrics,
                      verbose):  # pylint: disable=W0613
  """Verify that active models exist in DynamoDB

  :param engineMetrics: metric rows from Taurus Engine's respository
  :type engineMetrics: sequence of sqlalchemy.engine.RowProxy

  :param dynamodbMetrics: metric rows from Taurus Engine's dynamodb
    taurs.metric.<environment> table
  :type dynamodbMetrics: sequence of dict objects

  :param bool verbose: True for verbose mode

  :returns: a two-tuple (warnings, errors), where each of the elements is a
    sequence of two-tuples (caption, details)
  """
  errors = []

  activeModelsMap = {
    obj["uid"] : obj
    for obj in engineMetrics if obj["status"] == MetricStatus.ACTIVE
  }

  dynamodbModelsMap = {
    obj["uid"] : obj
    for obj in dynamodbMetrics
  }

  # Check if any active models are not in dynamodb
  inRepositoryButNotInDynamodb = set(activeModelsMap) - set(dynamodbModelsMap)

  if inRepositoryButNotInDynamodb:
    errors.append(
      (
        ("There are {} active models in Taurus Engine repository that are "
         "not in DynamoDB").format(len(inRepositoryButNotInDynamodb)),

        "\n".join(
          "ERROR - model not in DynamoDB: {}".format(
            [str(uid), str(activeModelsMap[uid]["name"])])
          for uid in inRepositoryButNotInDynamodb)
      )
    )


  # Check if any models are in dynamodb, but not among active models in Taurus
  # Engine's repository
  inDynamodbButNotInRepository = set(dynamodbModelsMap) - set(activeModelsMap)

  if inDynamodbButNotInRepository:
    errors.append(
      (
        ("There are {} model UIDs in DynamoDB that are not among active "
         "models in Taurus Engine repository").format(
           len(inDynamodbButNotInRepository)),

        "\n".join(
          "ERROR - model UID not in Taurus Engine repository: {}".format(
            [str(uid), str(dynamodbModelsMap[uid]["name"])])
          for uid in inDynamodbButNotInRepository)
      )
    )

  return [], errors



def _checkModelAttributeParity(engineMetrics,
                               dynamodbMetrics,
                               verbose):  # pylint: disable=W0613
  """Check attribute consistency between active models in DynamoDB and Taurus
  Engine's repository

  :param engineMetrics: metric rows from Taurus Engine's respository
  :type engineMetrics: sequence of sqlalchemy.engine.RowProxy

  :param dynamodbMetrics: metric rows from Taurus Engine's dynamodb
    taurs.metric.<environment> table
  :type dynamodbMetrics: sequence of dict objects

  :param bool verbose: True for verbose mode

  :returns: a two-tuple (warnings, errors), where each of the elements is a
    sequence of two-tuples (caption, details)
  """
  errors = []

  activeModelsMap = {
    obj["uid"] : obj
    for obj in engineMetrics if obj["status"] == MetricStatus.ACTIVE
  }

  dynamodbModelsMap = {
    obj["uid"] : obj
    for obj in dynamodbMetrics
  }

  commonMetricIds = set(dynamodbModelsMap) & set(activeModelsMap)

  mismatches = []

  for uid in commonMetricIds:
    diffs = []

    activeModel = activeModelsMap[uid]
    dynamodbModel = dynamodbModelsMap[uid]

    if activeModel["name"] != dynamodbModel["name"]:
      diffs.append(("name",
                    activeModel["name"],
                    dynamodbModel["name"]))

    if activeModel["server"] != dynamodbModel["display_name"]:
      diffs.append(("display_name",
                    activeModel["server"],
                    dynamodbModel["display_name"]))

    activeModelConfig = json.loads(activeModel["parameters"])
    activeModelUserInfo = activeModelConfig["metricSpec"]["userInfo"]

    if activeModelUserInfo["metricType"] != dynamodbModel["metricType"]:
      diffs.append(("metricType",
                    activeModelUserInfo["metricType"],
                    dynamodbModel["metricType"]))

    if activeModelUserInfo["metricTypeName"] != dynamodbModel["metricTypeName"]:
      diffs.append(("metricTypeName",
                    activeModelUserInfo["metricTypeName"],
                    dynamodbModel["metricTypeName"]))

    if activeModelUserInfo["symbol"] != dynamodbModel["symbol"]:
      diffs.append(("symbol",
                    activeModelUserInfo["symbol"],
                    dynamodbModel["symbol"]))

    if diffs:
      mismatches.append((uid, diffs))


  if mismatches:
    errors.append(
      (
        ("{} models have attribute mismatch between Taurus Engine "
         "repository and DynamoDB").format(len(mismatches)),

        "\n".join("ERROR - model {!s} has attribute mismatch in Engine vs "
                  "DynamoDB: {}".format(
                    uid,
                    [tuple(str(item) for item in diff) for diff in diffs])
                  for uid, diffs in mismatches)
      )
    )

  return [], errors



def _runAllChecks(engineMetrics, dynamodbMetrics, verbose):
  """Perform all consistency checks

  :param engineMetrics: metric rows from Taurus Engine's respository
  :type engineMetrics: sequence of sqlalchemy.engine.RowProxy

  :param dynamodbMetrics: metric rows from Taurus Engine's dynamodb
    taurs.metric.<environment> table
  :type dynamodbMetrics: sequence of dict objects

  :param bool verbose: True for verbose mode

  :returns: a two-tuple (warnings, errors), where each of the elements is a
    sequence of two-tuples (caption, details)
  """
  allWarnings = []
  allErrors = []

  if verbose:
    g_log.info("There are %s metrics in Taurus Engine repository",
               len(engineMetrics))

    g_log.info("There are %s metrics in DynamoDB",
               len(dynamodbMetrics))

    g_log.info("There are %s actively-monitored models (ACTIVE)",
               sum(1 for obj in engineMetrics
                   if obj["status"] == MetricStatus.ACTIVE))

    unmonitoredModels = tuple(obj for obj in engineMetrics
                              if obj["status"] == MetricStatus.UNMONITORED)
    if unmonitoredModels:
      g_log.info("There are %s metrics that are not monitored (UNMONITORED)",
                 len(unmonitoredModels))

    createPendingModels = tuple(obj for obj in engineMetrics
                                if obj["status"] == MetricStatus.CREATE_PENDING)
    if createPendingModels:
      g_log.info("There are %s models that are create-pending (CREATE_PENDING)",
                 len(createPendingModels))

    pendingDataModels = tuple(obj for obj in engineMetrics
                              if obj["status"] == MetricStatus.PENDING_DATA)
    if pendingDataModels:
      g_log.info("There are %s models that are pending-data (PENDING_DATA)",
                 len(pendingDataModels))


  # Check if any models are in error state
  warnings, errors = _checkFailedModels(engineMetrics=engineMetrics,
                                        verbose=verbose)
  allWarnings += warnings
  allErrors += errors


  # Verify that active models exist in DynamoDB
  warnings, errors = _checkModelParity(
    engineMetrics=engineMetrics,
    dynamodbMetrics=dynamodbMetrics,
    verbose=verbose)

  allWarnings += warnings
  allErrors += errors


  # Verify attribute consistency between active models in DynamoDB and Taurus
  # Engine's repository
  warnings, errors = _checkModelAttributeParity(engineMetrics=engineMetrics,
                                                dynamodbMetrics=dynamodbMetrics,
                                                verbose=verbose)

  allWarnings += warnings
  allErrors += errors


  return allWarnings, allErrors



def checkAndReport(verbose, warningsAsErrors):
  """Run checks and report findings

  :param bool verbose: True for verbose mode
  :param bool warningsAsErrors: True to treat warnings as errors, returning
    non-zero result code on warnings just as for errors.

  """
  # Read metric records from the engine's repository
  sqlEngine = repository.engineFactory()
  if verbose:
    g_log.info("Accessing Taurus Engine repository via %s", sqlEngine)


  with sqlEngine.connect() as conn:  # pylint: disable=E1101
    engineMetrics = repository.getAllMetrics(conn).fetchall()

  # Read metric records from the dynamodb taurs.metric.<environment> table
  dynamodbMetrics = _getMetricsFromDynamodb(verbose=verbose)


  # Perform all checks
  warnings, errors = _runAllChecks(engineMetrics=engineMetrics,
                                   dynamodbMetrics=dynamodbMetrics,
                                   verbose=verbose)


  # Report findings
  if warnings or errors or verbose:

    for caption, details in warnings:
      g_log.warn("%s\n%s", caption, details)

    for caption, details in errors:
      g_log.error("%s\n%s", caption, details)

    g_log.info("--------- SUMMARY ---------")

    for caption, _ in warnings:
      g_log.warn(caption)

    for caption, _ in errors:
      g_log.error(caption)

    if warnings:
      g_log.warn("Warnings: %s", len(warnings))
    elif verbose:
      g_log.info("Warnings: 0")

    if errors:
      g_log.error("Errors: %s", len(errors))
    elif verbose:
      g_log.info("Errors: 0")


  if errors or (warnings and warningsAsErrors):
    return 1
  else:
    return 0



@logExceptions(g_log)
def main(args=sys.argv[1:]):
  """Console Script entry point

  :param list args: The equivalent of `sys.argv[1:]`
  """

  logging_support.LoggingSupport.initTool()


  try:
    args = _parseArgs(args)
  except SystemExit as exc:
    if exc.code == 0:
      # Suppress exception logging when exiting due to --help
      return 0

    raise


  return checkAndReport(verbose=args.verbose,
                        warningsAsErrors=args.warningsAsErrors)



if __name__ == "__main__":

  raise NotImplementedError("Use console entry point script instead")
