import copy
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Optional, Tuple, Union

import pandas as pd
from ruamel.yaml import YAML

import great_expectations.exceptions as ge_exceptions
from great_expectations.core.batch import BatchMarkers, BatchSpec
from great_expectations.core.util import AzureUrl, DBFSPath, GCSUrl, S3Url
from great_expectations.expectations.registry import get_metric_provider
from great_expectations.util import filter_properties_dict
from great_expectations.validator.metric_configuration import MetricConfiguration

logger = logging.getLogger(__name__)
yaml = YAML()
yaml.default_flow_style = False


class NoOpDict:
    def __getitem__(self, item):
        return None

    def __setitem__(self, key, value):
        return None

    def update(self, value):
        return None


class BatchData:
    def __init__(self, execution_engine):
        self._execution_engine = execution_engine

    @property
    def execution_engine(self):
        return self._execution_engine

    def head(self, *args, **kwargs):
        # CONFLICT ON PURPOSE. REMOVE.
        return pd.DataFrame({})


class MetricFunctionTypes(Enum):
    VALUE = "value"
    MAP_VALUES = "value"  # "map_values"
    WINDOW_VALUES = "value"  # "window_values"
    AGGREGATE_VALUE = "value"  # "aggregate_value"


class MetricDomainTypes(Enum):
    COLUMN = "column"
    COLUMN_PAIR = "column_pair"
    MULTICOLUMN = "multicolumn"
    TABLE = "table"


class DataConnectorStorageDataReferenceResolver:
    DATA_CONNECTOR_NAME_TO_STORAGE_NAME_MAP: Dict[str, str] = {
        "InferredAssetS3DataConnector": "S3",
        "ConfiguredAssetS3DataConnector": "S3",
        "InferredAssetGCSDataConnector": "GCS",
        "ConfiguredAssetGCSDataConnector": "GCS",
        "InferredAssetAzureDataConnector": "ABS",
        "ConfiguredAssetAzureDataConnector": "ABS",
        "InferredAssetDBFSDataConnector": "DBFS",
        "ConfiguredAssetDBFSDataConnector": "DBFS",
    }
    STORAGE_NAME_EXECUTION_ENGINE_NAME_PATH_RESOLVERS: Dict[
        Tuple[str, str], Callable
    ] = {
        (
            "S3",
            "PandasExecutionEngine",
        ): lambda template_arguments: S3Url.OBJECT_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "S3",
            "SparkDFExecutionEngine",
        ): lambda template_arguments: S3Url.OBJECT_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "GCS",
            "PandasExecutionEngine",
        ): lambda template_arguments: GCSUrl.OBJECT_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "GCS",
            "SparkDFExecutionEngine",
        ): lambda template_arguments: GCSUrl.OBJECT_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "ABS",
            "PandasExecutionEngine",
        ): lambda template_arguments: AzureUrl.AZURE_BLOB_STORAGE_HTTPS_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "ABS",
            "SparkDFExecutionEngine",
        ): lambda template_arguments: AzureUrl.AZURE_BLOB_STORAGE_WASBS_URL_TEMPLATE.format(
            **template_arguments
        ),
        (
            "DBFS",
            "SparkDFExecutionEngine",
        ): lambda template_arguments: DBFSPath.convert_to_protocol_version(
            **template_arguments
        ),
        (
            "DBFS",
            "PandasExecutionEngine",
        ): lambda template_arguments: DBFSPath.convert_to_file_semantics_version(
            **template_arguments
        ),
    }

    @staticmethod
    def resolve_data_reference(
        data_connector_name: str,
        execution_engine_name: str,
        template_arguments: dict,
    ):
        """Resolve file path for a (data_connector_name, execution_engine_name) combination."""
        storage_name: str = DataConnectorStorageDataReferenceResolver.DATA_CONNECTOR_NAME_TO_STORAGE_NAME_MAP[
            data_connector_name
        ]
        return DataConnectorStorageDataReferenceResolver.STORAGE_NAME_EXECUTION_ENGINE_NAME_PATH_RESOLVERS[
            (storage_name, execution_engine_name)
        ](
            template_arguments
        )


class ExecutionEngine(ABC):
    recognized_batch_spec_defaults = set()

    def __init__(
        self,
        name=None,
        caching=True,
        batch_spec_defaults=None,
        batch_data_dict=None,
        validator=None,
    ):
        self.name = name
        self._validator = validator

        # NOTE: using caching makes the strong assumption that the user will not modify the core data store
        # (e.g. self.spark_df) over the lifetime of the dataset instance
        self._caching = caching
        # NOTE: 20200918 - this is a naive cache; update.
        if self._caching:
            self._metric_cache = {}
        else:
            self._metric_cache = NoOpDict()

        if batch_spec_defaults is None:
            batch_spec_defaults = {}
        batch_spec_defaults_keys = set(batch_spec_defaults.keys())
        if not batch_spec_defaults_keys <= self.recognized_batch_spec_defaults:
            logger.warning(
                "Unrecognized batch_spec_default(s): %s"
                % str(batch_spec_defaults_keys - self.recognized_batch_spec_defaults)
            )

        self._batch_spec_defaults = {
            key: value
            for key, value in batch_spec_defaults.items()
            if key in self.recognized_batch_spec_defaults
        }

        self._batch_data_dict = {}
        if batch_data_dict is None:
            batch_data_dict = {}
        self._active_batch_data_id = None
        self._load_batch_data_from_dict(batch_data_dict)

        # Gather the call arguments of the present function (and add the "class_name"), filter out the Falsy values, and
        # set the instance "_config" variable equal to the resulting dictionary.
        self._config = {
            "name": name,
            "caching": caching,
            "batch_spec_defaults": batch_spec_defaults,
            "batch_data_dict": batch_data_dict,
            "validator": validator,
            "module_name": self.__class__.__module__,
            "class_name": self.__class__.__name__,
        }
        filter_properties_dict(properties=self._config, clean_falsy=True, inplace=True)

    def configure_validator(self, validator):
        """Optionally configure the validator as appropriate for the execution engine."""
        pass

    @property
    def active_batch_data_id(self):
        """The batch id for the default batch data.

        When an execution engine is asked to process a compute domain that does
        not include a specific batch_id, then the data associated with the
        active_batch_data_id will be used as the default.
        """
        if self._active_batch_data_id is not None:
            return self._active_batch_data_id
        elif len(self.loaded_batch_data_dict) == 1:
            return list(self.loaded_batch_data_dict.keys())[0]
        else:
            return None

    @active_batch_data_id.setter
    def active_batch_data_id(self, batch_id):
        if batch_id in self.loaded_batch_data_dict.keys():
            self._active_batch_data_id = batch_id
        else:
            raise ge_exceptions.ExecutionEngineError(
                f"Unable to set active_batch_data_id to {batch_id}. The may data may not be loaded."
            )

    @property
    def active_batch_data(self):
        """The data from the currently-active batch."""
        if self.active_batch_data_id is None:
            return None
        else:
            return self.loaded_batch_data_dict.get(self.active_batch_data_id)

    @property
    def loaded_batch_data_dict(self):
        """The current dictionary of batches."""
        return self._batch_data_dict

    @property
    def loaded_batch_data_ids(self):
        return list(self.loaded_batch_data_dict.keys())

    @property
    def config(self) -> dict:
        return self._config

    @property
    def dialect(self):
        return None

    def get_batch_data(
        self,
        batch_spec: BatchSpec,
    ) -> Any:
        """Interprets batch_data and returns the appropriate data.

        This method is primarily useful for utility cases (e.g. testing) where
        data is being fetched without a DataConnector and metadata like
        batch_markers is unwanted

        Note: this method is currently a thin wrapper for get_batch_data_and_markers.
        It simply suppresses the batch_markers.
        """
        batch_data, _ = self.get_batch_data_and_markers(batch_spec)
        return batch_data

    @abstractmethod
    def get_batch_data_and_markers(self, batch_spec) -> Tuple[BatchData, BatchMarkers]:
        raise NotImplementedError

    def load_batch_data(self, batch_id: str, batch_data: Any) -> None:
        """
        Loads the specified batch_data into the execution engine
        """
        self._batch_data_dict[batch_id] = batch_data
        self._active_batch_data_id = batch_id

    def _load_batch_data_from_dict(self, batch_data_dict):
        """
        Loads all data in batch_data_dict into load_batch_data
        """
        for batch_id, batch_data in batch_data_dict.items():
            self.load_batch_data(batch_id, batch_data)

    def resolve_metrics(
        self,
        metrics_to_resolve: Iterable[MetricConfiguration],
        metrics: Optional[Dict[Tuple[str, str, str], MetricConfiguration]] = None,
        runtime_configuration: Optional[dict] = None,
    ) -> Dict[Tuple[str, str, str], Any]:
        """resolve_metrics is the main entrypoint for an execution engine. The execution engine will compute the value
        of the provided metrics.

        Args:
            metrics_to_resolve: the metrics to evaluate
            metrics: already-computed metrics currently available to the engine
            runtime_configuration: runtime configuration information

        Returns:
            resolved_metrics (Dict): a dictionary with the values for the metrics that have just been resolved.
        """
        if metrics is None:
            metrics = {}

        resolved_metrics: Dict[Tuple[str, str, str], Any] = {}

        metric_fn_bundle = []
        for metric_to_resolve in metrics_to_resolve:
            metric_dependencies = {}
            for k, v in metric_to_resolve.metric_dependencies.items():
                if v.id in metrics:
                    metric_dependencies[k] = metrics[v.id]
                elif self._caching and v.id in self._metric_cache:
                    metric_dependencies[k] = self._metric_cache[v.id]
                else:
                    raise ge_exceptions.MetricError(
                        message=f'Missing metric dependency: {str(k)} for metric "{metric_to_resolve.metric_name}".'
                    )

            metric_class, metric_fn = get_metric_provider(
                metric_name=metric_to_resolve.metric_name, execution_engine=self
            )
            metric_provider_kwargs = {
                "cls": metric_class,
                "execution_engine": self,
                "metric_domain_kwargs": metric_to_resolve.metric_domain_kwargs,
                "metric_value_kwargs": metric_to_resolve.metric_value_kwargs,
                "metrics": metric_dependencies,
                "runtime_configuration": runtime_configuration,
            }
            if metric_fn is None:
                try:
                    (
                        metric_fn,
                        compute_domain_kwargs,
                        accessor_domain_kwargs,
                    ) = metric_dependencies.pop("metric_partial_fn")
                except KeyError as e:
                    raise ge_exceptions.MetricError(
                        message=f'Missing metric dependency: {str(e)} for metric "{metric_to_resolve.metric_name}".'
                    )
                metric_fn_bundle.append(
                    (
                        metric_to_resolve,
                        metric_fn,
                        compute_domain_kwargs,
                        accessor_domain_kwargs,
                        metric_provider_kwargs,
                    )
                )
                continue
            metric_fn_type = getattr(
                metric_fn, "metric_fn_type", MetricFunctionTypes.VALUE
            )
            if metric_fn_type in [
                MetricPartialFunctionTypes.MAP_FN,
                MetricPartialFunctionTypes.MAP_CONDITION_FN,
                MetricPartialFunctionTypes.WINDOW_FN,
                MetricPartialFunctionTypes.WINDOW_CONDITION_FN,
                MetricPartialFunctionTypes.AGGREGATE_FN,
            ]:
                # NOTE: 20201026 - JPC - we could use the fact that these metric functions return functions rather
                # than data to optimize compute in the future
                try:
                    resolved_metrics[metric_to_resolve.id] = metric_fn(
                        **metric_provider_kwargs
                    )
                except Exception as e:
                    raise ge_exceptions.MetricResolutionError(
                        message=str(e), failed_metrics=(metric_to_resolve,)
                    )
            elif metric_fn_type in [
                MetricFunctionTypes.VALUE,
                MetricPartialFunctionTypes.MAP_SERIES,
                MetricPartialFunctionTypes.MAP_CONDITION_SERIES,
            ]:
                try:
                    resolved_metrics[metric_to_resolve.id] = metric_fn(
                        **metric_provider_kwargs
                    )
                except Exception as e:
                    raise ge_exceptions.MetricResolutionError(
                        message=str(e), failed_metrics=(metric_to_resolve,)
                    )
            else:
                logger.warning(
                    f"Unrecognized metric function type while trying to resolve {str(metric_to_resolve.id)}"
                )
                try:
                    resolved_metrics[metric_to_resolve.id] = metric_fn(
                        **metric_provider_kwargs
                    )
                except Exception as e:
                    raise ge_exceptions.MetricResolutionError(
                        message=str(e), failed_metrics=(metric_to_resolve,)
                    )
        if len(metric_fn_bundle) > 0:
            try:
                new_resolved = self.resolve_metric_bundle(metric_fn_bundle)
                resolved_metrics.update(new_resolved)
            except Exception as e:
                raise ge_exceptions.MetricResolutionError(
                    message=str(e), failed_metrics=[x[0] for x in metric_fn_bundle]
                )
        if self._caching:
            self._metric_cache.update(resolved_metrics)

        return resolved_metrics

    def resolve_metric_bundle(self, metric_fn_bundle):
        """Resolve a bundle of metrics with the same compute domain as part of a single trip to the compute engine."""
        raise NotImplementedError

    def get_domain_records(
        self,
        domain_kwargs: dict,
    ) -> Any:
        """
        get_domain_records computes the full-access data (dataframe or selectable) for computing metrics based on the
        given domain_kwargs and specific engine semantics.

        Returns:
            data corresponding to the compute domain
        """

        raise NotImplementedError

    def get_compute_domain(
        self,
        domain_kwargs: dict,
        domain_type: Union[str, MetricDomainTypes],
    ) -> Tuple[Any, dict, dict]:
        """get_compute_domain computes the optimal domain_kwargs for computing metrics based on the given domain_kwargs
        and specific engine semantics.

        Returns:
            A tuple consisting of three elements:

            1. data corresponding to the compute domain;
            2. a modified copy of domain_kwargs describing the domain of the data returned in (1);
            3. a dictionary describing the access instructions for data elements included in the compute domain
                (e.g. specific column name).

            In general, the union of the compute_domain_kwargs and accessor_domain_kwargs will be the same as the domain_kwargs
            provided to this method.
        """

        raise NotImplementedError

    def add_column_row_condition(
        self, domain_kwargs, column_name=None, filter_null=True, filter_nan=False
    ):
        """EXPERIMENTAL

        Add a row condition for handling null filter.

        Args:
            domain_kwargs: the domain kwargs to use as the base and to which to add the condition
            column_name: if provided, use this name to add the condition; otherwise, will use "column" key from table_domain_kwargs
            filter_null: if true, add a filter for null values
            filter_nan: if true, add a filter for nan values
        """
        if filter_null is False and filter_nan is False:
            logger.warning(
                "add_column_row_condition called with no filter condition requested"
            )
            return domain_kwargs

        if filter_nan:
            raise ge_exceptions.GreatExpectationsError(
                "Base ExecutionEngine does not support adding nan condition filters"
            )

        if "row_condition" in domain_kwargs and domain_kwargs["row_condition"]:
            raise ge_exceptions.GreatExpectationsError(
                "ExecutionEngine does not support updating existing row_conditions."
            )

        new_domain_kwargs = copy.deepcopy(domain_kwargs)
        assert "column" in domain_kwargs or column_name is not None
        if column_name is not None:
            column = column_name
        else:
            column = domain_kwargs["column"]
        new_domain_kwargs["condition_parser"] = "great_expectations__experimental__"
        new_domain_kwargs["row_condition"] = f'col("{column}").notnull()'
        return new_domain_kwargs

    def resolve_data_reference(
        self, data_connector_name: str, template_arguments: dict
    ):
        """Resolve file path for a (data_connector_name, execution_engine_name) combination."""
        return DataConnectorStorageDataReferenceResolver.resolve_data_reference(
            data_connector_name=data_connector_name,
            execution_engine_name=self.__class__.__name__,
            template_arguments=template_arguments,
        )

    @staticmethod
    def _split_table_metric_domain_kwargs(
        domain_kwargs: Dict,
        domain_type: Union[str, MetricDomainTypes],
        accessor_keys: Optional[Iterable[str]] = None,
    ) -> Tuple[Dict, Dict]:
        """Split domain_kwargs for table domain types into compute and accessor domain kwargs.

        Args:
            domain_kwargs: A dictionary consisting of the domain kwargs specifying which data to obtain
            domain_type: an Enum value indicating which metric domain the user would
            like to be using, or a corresponding string value representing it. String types include "identity",
            "column", "column_pair", "table" and "other". Enum types include capitalized versions of these from the
            class MetricDomainTypes.
            accessor_keys: keys that are part of the compute domain but should be ignored when
            describing the domain and simply transferred with their associated values into accessor_domain_kwargs.

        Returns:
            compute_domain_kwargs, accessor_domain_kwargs from domain_kwargs
            The union of compute_domain_kwargs, accessor_domain_kwargs is the input domain_kwargs
        """
        # Extracting value from enum if it is given for future computation
        domain_type = MetricDomainTypes(domain_type)
        assert (
            domain_type == MetricDomainTypes.TABLE
        ), "This method only supports MetricDomainTypes.TABLE"

        compute_domain_kwargs: Dict = copy.deepcopy(domain_kwargs)
        accessor_domain_kwargs: Dict = {}

        if accessor_keys is not None and len(list(accessor_keys)) > 0:
            for key in accessor_keys:
                accessor_domain_kwargs[key] = compute_domain_kwargs.pop(key)
        if len(domain_kwargs.keys()) > 0:
            # Warn user if kwarg not "normal".
            unexpected_keys: set = set(compute_domain_kwargs.keys()).difference(
                {
                    "batch_id",
                    "table",
                    "row_condition",
                    "condition_parser",
                }
            )
            if len(unexpected_keys) > 0:
                unexpected_keys_str: str = ", ".join(
                    map(lambda element: f'"{element}"', unexpected_keys)
                )
                logger.warning(
                    f'Unexpected key(s) {unexpected_keys_str} found in domain_kwargs for domain type "{domain_type.value}".'
                )
        return compute_domain_kwargs, accessor_domain_kwargs


class MetricPartialFunctionTypes(Enum):
    MAP_FN = "map_fn"
    MAP_SERIES = "map_series"
    MAP_CONDITION_FN = "map_condition_fn"
    MAP_CONDITION_SERIES = "map_condition_series"
    WINDOW_FN = "window_fn"
    WINDOW_CONDITION_FN = "window_condition_fn"
    AGGREGATE_FN = "aggregate_fn"

    @property
    def metric_suffix(self):
        if self.name in ["MAP_FN", "MAP_SERIES", "WINDOW_FN"]:
            return "map"
        elif self.name in [
            "MAP_CONDITION_FN",
            "MAP_CONDITION_SERIES",
            "WINDOW_CONDITION_FN",
        ]:
            return "condition"
        elif self.name in ["AGGREGATE_FN"]:
            return "aggregate_fn"
