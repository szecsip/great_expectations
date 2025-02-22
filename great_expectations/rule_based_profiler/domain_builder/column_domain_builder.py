from typing import List, Optional, Union

import great_expectations.exceptions as ge_exceptions
from great_expectations.core.batch import Batch, BatchRequest, RuntimeBatchRequest
from great_expectations.execution_engine.execution_engine import MetricDomainTypes
from great_expectations.rule_based_profiler.domain_builder import DomainBuilder
from great_expectations.rule_based_profiler.helpers.util import (
    build_simple_domains_from_column_names,
    get_parameter_value_and_validate_return_type,
)
from great_expectations.rule_based_profiler.types import Domain, ParameterContainer
from great_expectations.validator.metric_configuration import MetricConfiguration


class ColumnDomainBuilder(DomainBuilder):
    def __init__(
        self,
        batch_list: Optional[List[Batch]] = None,
        batch_request: Optional[Union[BatchRequest, RuntimeBatchRequest, dict]] = None,
        data_context: Optional["DataContext"] = None,  # noqa: F821
        column_names: Optional[Union[str, Optional[List[str]]]] = None,
    ):
        """
        Args:
            batch_list: explicitly specified Batch objects for use in DomainBuilder
            batch_request: specified in DomainBuilder configuration to get Batch objects for domain computation.
            data_context: DataContext
            column_names: Explicitly specified column_names list desired (if None, it is computed based on active Batch)
        """
        super().__init__(
            batch_list=batch_list,
            batch_request=batch_request,
            data_context=data_context,
        )

        self._column_names = column_names

    @property
    def domain_type(self) -> Union[str, MetricDomainTypes]:
        return MetricDomainTypes.COLUMN

    """
    All DomainBuilder classes, whose "domain_type" property equals "MetricDomainTypes.COLUMN", must extend present class
    (ColumnDomainBuilder) in order to provide full getter/setter accessor for "column_names" property (as override).
    """

    @property
    def column_names(self) -> Optional[Union[str, Optional[List[str]]]]:
        return self._column_names

    @column_names.setter
    def column_names(self, value: Optional[Union[str, Optional[List[str]]]]) -> None:
        self._column_names = value

    def get_effective_column_names(
        self,
        include_columns: Optional[Union[str, Optional[List[str]]]] = None,
        exclude_columns: Optional[Union[str, Optional[List[str]]]] = None,
        variables: Optional[ParameterContainer] = None,
    ) -> List[str]:
        # Obtain include_columns from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        include_columns = get_parameter_value_and_validate_return_type(
            domain=None,
            parameter_reference=include_columns,
            expected_return_type=None,
            variables=variables,
            parameters=None,
        )

        # Obtain exclude_columns from "rule state" (i.e., variables and parameters); from instance variable otherwise.
        exclude_columns = get_parameter_value_and_validate_return_type(
            domain=None,
            parameter_reference=exclude_columns,
            expected_return_type=None,
            variables=variables,
            parameters=None,
        )

        batch_ids: List[str] = self.get_batch_ids(variables=variables)

        table_columns: List[str] = self.get_validator(variables=variables).get_metric(
            metric=MetricConfiguration(
                metric_name="table.columns",
                metric_domain_kwargs={
                    "batch_id": batch_ids[-1],  # active_batch_id
                },
                metric_value_kwargs=None,
                metric_dependencies=None,
            )
        )

        effective_column_names: List[str] = include_columns or table_columns

        if exclude_columns is None:
            exclude_columns = []

        column_name: str
        effective_column_names = [
            column_name
            for column_name in effective_column_names
            if column_name not in exclude_columns
        ]

        if set(effective_column_names) == set(table_columns):
            return effective_column_names

        column_name: str
        for column_name in effective_column_names:
            if column_name not in table_columns:
                raise ge_exceptions.ProfilerExecutionError(
                    message=f'Error: The column "{column_name}" in BatchData does not exist.'
                )

        return effective_column_names

    def _get_domains(
        self,
        variables: Optional[ParameterContainer] = None,
    ) -> List[Domain]:
        """
        Obtains and returns domains for all columns of a table (or for configured columns, if they exist in the table).
        """
        return build_simple_domains_from_column_names(
            column_names=self.get_effective_column_names(
                include_columns=self.column_names,
                exclude_columns=None,
                variables=variables,
            ),
            domain_type=self.domain_type,
        )
