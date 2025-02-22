from great_expectations.core.usage_statistics.anonymizers.action_anonymizer import (
    ActionAnonymizer,
)
from great_expectations.core.usage_statistics.anonymizers.anonymizer import Anonymizer
from great_expectations.validation_operators import (
    ActionListValidationOperator,
    ValidationOperator,
    WarningAndFailureExpectationSuitesValidationOperator,
)


class ValidationOperatorAnonymizer(Anonymizer):
    def __init__(self, salt=None):
        super().__init__(salt=salt)
        # ordered bottom up in terms of inheritance order
        self._ge_classes = [
            ActionListValidationOperator,
            WarningAndFailureExpectationSuitesValidationOperator,
            ValidationOperator,
        ]
        self._action_anonymizer = ActionAnonymizer(salt=salt)

    def anonymize_validation_operator_info(
        self, validation_operator_name, validation_operator_obj
    ):
        anonymized_info_dict: dict = {
            "anonymized_name": self.anonymize(validation_operator_name)
        }
        actions_dict: dict = validation_operator_obj.actions

        anonymized_info_dict.update(
            self.anonymize_object_info(
                object_=validation_operator_obj,
                anonymized_info_dict=anonymized_info_dict,
            )
        )

        if actions_dict:
            anonymized_info_dict["anonymized_action_list"] = [
                self._action_anonymizer.anonymize_action_info(
                    action_name=action_name, action_obj=action_obj
                )
                for action_name, action_obj in actions_dict.items()
            ]

        return anonymized_info_dict
