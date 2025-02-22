from typing import Any, Dict, List, Optional, Tuple, Union

from great_expectations.core.batch import Batch, BatchRequest, RuntimeBatchRequest
from great_expectations.rule_based_profiler.domain_builder import ColumnDomainBuilder
from great_expectations.rule_based_profiler.helpers.util import (
    build_simple_domains_from_column_names,
    get_parameter_value_and_validate_return_type,
    get_resolved_metrics_by_key,
)
from great_expectations.rule_based_profiler.types import Domain, ParameterContainer
from great_expectations.validator.metric_configuration import MetricConfiguration


class MapMetricColumnDomainBuilder(ColumnDomainBuilder):
    """
    This DomainBuilder uses relative tolerance of specified map metric to identify domains.
    """

    def __init__(
        self,
        map_metric_name: str,
        batch_list: Optional[List[Batch]] = None,
        batch_request: Optional[Union[BatchRequest, RuntimeBatchRequest, dict]] = None,
        data_context: Optional["DataContext"] = None,  # noqa: F821
        column_names: Optional[Union[str, Optional[List[str]]]] = None,
        max_unexpected_values: Union[str, int] = 0,
        max_unexpected_ratio: Optional[Union[str, float]] = None,
        min_max_unexpected_values_proportion: Union[str, float] = 9.75e-1,
    ):
        """
        Create column domains using tolerance for inter-Batch proportion of adherence to intra-Batch "unexpected_count"
        value of specified "map_metric_name" as criterion for emitting Domain for column under consideration.

        Args:
            map_metric_name: the name of a map metric (must be a supported and registered map metric); the suffix
            ".unexpected_count" will be appended to "map_metric_name" to be used in MetricConfiguration to get values.
            batch_list: explicitly specified Batch objects for use in DomainBuilder
            batch_request: BatchRequest to be optionally used to define batches to consider for this domain builder.
            data_context: DataContext associated with this profiler.
            column_names: Explicitly specified column_names list desired (if None, it is computed based on active Batch)
            max_unexpected_values: maximum "unexpected_count" value of "map_metric_name" (intra-Batch)
            max_unexpected_ratio: maximum "unexpected_count" value of "map_metric_name" divided by number of records
            (intra-Batch); if both "max_unexpected_values" and "max_unexpected_ratio" are specified, then
            "max_unexpected_ratio" is used (and "max_unexpected_values" is ignored).
            min_max_unexpected_values_proportion: minimum fraction of Batch objects adhering to "max_unexpected_values"

        For example (using default values of "max_unexpected_values" and "min_max_unexpected_values_proportion"):
        Suppose that "map_metric_name" is "column_values.nonnull" and consider the following three Batches of data:

        Batch-0        Batch-1        Batch-2
        A B C          A B C          A B C
        1 1 2          1 1 2          1 1 2
        1 2 3          1 2 3          1 2 3
        1 1 2            1 2          1 1 2
        2 2 2          2 2 2          2 2 2
        3 2 3          3 2 3          3 2 3

        and consider adherence to this map metric for column "A".

        The intra-Batch adherence to "max_unexpected_values" being 0 gives the following result (1 is True, 0 is False):

        Batch-0        Batch-1        Batch-2
        1              0              1

        That gives the inter-Batch adherence fraction of 2/3 (0.67).  Since 1/3 >= min_max_unexpected_values_proportion
        evaluates to False, column "A" does not pass the tolerance test, and thus Domain for it will not be emitted.

        However, if "max_unexpected_ratio" is eased to above 0.2, then the tolerances will be met and Domain emitted.
        Alternatively, if "min_max_unexpected_values_proportion" is lowered to 0.66, Domain will also be emitted.
        """
        super().__init__(
            batch_list=batch_list,
            batch_request=batch_request,
            data_context=data_context,
            column_names=column_names,
        )

        self._map_metric_name = map_metric_name
        self._max_unexpected_values = max_unexpected_values
        self._max_unexpected_ratio = max_unexpected_ratio
        self._min_max_unexpected_values_proportion = (
            min_max_unexpected_values_proportion
        )

    @property
    def map_metric_name(self) -> str:
        return self._map_metric_name

    @property
    def max_unexpected_values(self) -> Union[str, int]:
        return self._max_unexpected_values

    @property
    def max_unexpected_ratio(self) -> Optional[Union[str, float]]:
        return self._max_unexpected_ratio

    @property
    def min_max_unexpected_values_proportion(self) -> Optional[Union[str, float]]:
        return self._min_max_unexpected_values_proportion

    def _get_domains(
        self,
        variables: Optional[ParameterContainer] = None,
    ) -> List[Domain]:
        """Return domains matching the specified tolerance limits.

        Args:
            variables: Optional variables to substitute when evaluating.

        Returns:
            List of domains that match the desired tolerance limits.
        """
        # Obtain map_metric_name from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        map_metric_name: str = get_parameter_value_and_validate_return_type(
            domain=None,
            parameter_reference=self.map_metric_name,
            expected_return_type=str,
            variables=variables,
            parameters=None,
        )

        # Obtain max_unexpected_values from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        max_unexpected_values: int = get_parameter_value_and_validate_return_type(
            domain=None,
            parameter_reference=self.max_unexpected_values,
            expected_return_type=int,
            variables=variables,
            parameters=None,
        )

        # Obtain max_unexpected_ratio from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        max_unexpected_ratio: Optional[
            float
        ] = get_parameter_value_and_validate_return_type(
            domain=None,
            parameter_reference=self.max_unexpected_ratio,
            expected_return_type=None,
            variables=variables,
            parameters=None,
        )

        # Obtain min_max_unexpected_values_proportion from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        min_max_unexpected_values_proportion: float = (
            get_parameter_value_and_validate_return_type(
                domain=None,
                parameter_reference=self.min_max_unexpected_values_proportion,
                expected_return_type=float,
                variables=variables,
                parameters=None,
            )
        )

        table_column_names: List[str] = self.get_effective_column_names(
            include_columns=self.column_names,
            exclude_columns=None,
            variables=variables,
        )

        validator: "Validator" = self.get_validator(variables=variables)  # noqa: F821

        batch_ids: List[str] = self.get_batch_ids(variables=variables)
        num_batch_ids: int = len(batch_ids)

        table_row_counts: Dict[str, int] = self.get_table_row_counts(
            validator=validator,
            batch_ids=batch_ids,
            variables=variables,
        )
        mean_table_row_count_as_float: float = (
            1.0 * sum(table_row_counts.values()) / num_batch_ids
        )

        # If no "max_unexpected_ratio" is given, compute it based on average number of records across all Batch objects.
        if max_unexpected_ratio is None:
            max_unexpected_ratio = max_unexpected_values / mean_table_row_count_as_float

        metric_configurations_by_column_name: Dict[
            str, List[MetricConfiguration]
        ] = self._generate_metric_configurations(
            map_metric_name=map_metric_name,
            batch_ids=batch_ids,
            column_names=table_column_names,
        )

        candidate_column_names: List[
            str
        ] = self._get_column_names_satisfying_tolerance_limits(
            validator=validator,
            num_batch_ids=num_batch_ids,
            metric_configurations_by_column_name=metric_configurations_by_column_name,
            mean_table_row_count_as_float=mean_table_row_count_as_float,
            max_unexpected_ratio=max_unexpected_ratio,
            min_max_unexpected_values_proportion=min_max_unexpected_values_proportion,
        )

        return build_simple_domains_from_column_names(
            column_names=candidate_column_names,
            domain_type=self.domain_type,
        )

    @staticmethod
    def _generate_metric_configurations(
        map_metric_name: str,
        batch_ids: List[str],
        column_names: List[str],
    ) -> Dict[str, List[MetricConfiguration]]:
        """
        Generate metric configurations used to compute "unexpected_count" values for "map_metric_name".

        Args:
            map_metric_name: the name of a map metric (must be a supported and registered map metric); the suffix
            ".unexpected_count" will be appended to "map_metric_name" to be used in MetricConfiguration to get values.
            batch_ids: List of batch_ids used to create metric configurations.
            column_names: List of column_names used to create metric configurations.

        Returns:
            Dictionary of the form {
                column_name: List[MetricConfiguration],
            }
        """
        column_name: str
        batch_id: str
        metric_configurations: Dict[str, List[MetricConfiguration]] = {
            column_name: [
                MetricConfiguration(
                    metric_name=f"{map_metric_name}.unexpected_count",
                    metric_domain_kwargs={
                        "column": column_name,
                        "batch_id": batch_id,
                    },
                    metric_value_kwargs=None,
                    metric_dependencies=None,
                )
                for batch_id in batch_ids
            ]
            for column_name in column_names
        }

        return metric_configurations

    @staticmethod
    def _get_column_names_satisfying_tolerance_limits(
        validator: "Validator",  # noqa: F821
        num_batch_ids: int,
        metric_configurations_by_column_name: Dict[str, List[MetricConfiguration]],
        mean_table_row_count_as_float: float,
        max_unexpected_ratio: float,
        min_max_unexpected_values_proportion: float,
    ) -> List[str]:
        """
        Compute figures of merit and return column names satisfying tolerance limits.

        Args:
            validator: Validator used to compute column cardinality.
            metric_configurations_by_column_name: metric configurations used to compute figures of merit.
            mean_table_row_count_as_float: average number of records over available Batch objects.
            max_unexpected_ratio: maximum "unexpected_count" value of "map_metric_name" averaged over numbers of records
            min_max_unexpected_values_proportion: minimum fraction of Batch objects adhering to "max_unexpected_ratio"

        Returns:
            List of column names satisfying tolerance limits.
        """
        column_name: str
        resolved_metrics: Dict[Tuple[str, str, str], Any]

        resolved_metrics_by_column_name: Dict[
            str, Dict[Tuple[str, str, str], Any]
        ] = get_resolved_metrics_by_key(
            validator=validator,
            metric_configurations_by_key=metric_configurations_by_column_name,
        )

        metric_value: Any
        intra_batch_unexpected_ratios_by_column_name: Dict[str, List[float]] = {
            column_name: [
                metric_value / mean_table_row_count_as_float
                for metric_value in resolved_metrics.values()
            ]
            for column_name, resolved_metrics in resolved_metrics_by_column_name.items()
        }

        metric_value_ratio: float
        intra_batch_adherence_by_column_name: Dict[str, List[bool]] = {
            column_name: [
                metric_value_ratio <= max_unexpected_ratio
                for metric_value_ratio in intra_batch_unexpected_ratios_by_column_name[
                    column_name
                ]
            ]
            for column_name in intra_batch_unexpected_ratios_by_column_name.keys()
        }

        inter_batch_adherence_by_column_name: Dict[str, float] = {
            column_name: 1.0
            * sum(intra_batch_adherence_by_column_name[column_name])
            / num_batch_ids
            for column_name in intra_batch_adherence_by_column_name.keys()
        }

        inter_batch_unexpected_values_proportion: float
        candidate_column_names: List[str] = [
            column_name
            for column_name, inter_batch_unexpected_values_proportion in inter_batch_adherence_by_column_name.items()
            if inter_batch_unexpected_values_proportion
            >= min_max_unexpected_values_proportion
        ]

        return candidate_column_names
