from jsonschema import ValidationError
import logging
logger = logging.getLogger(__name__)

class Keywords:
    """storage for custom schema keyword definitions
    registry of new keywords is automated
    docstrings for each keyword definition should elaborate on the purpose, location, and value for the keyword
    """

    @staticmethod
    def registry() -> dict:
        """return a dict of function name: function for all custom validators"""
        return {key:getattr(Keywords, key) for key in Keywords.__dict__.keys() if callable(getattr(Keywords, key)) and not key.startswith("__") and not key == "registry"}

    @staticmethod
    def wwpdb_resolution_comparator(validator:object, comparator_value:bool, instance:dict, schema:object):
        """ensure that refinement high resolution is less than or equal to experimental high resolution
        insert keyword wwpdb_resolution_comparator outside of relevant properties section at same level as required properties
        value for keyword is ignored but should be set to true
        args:
            validator (object): the json schema validator object
            comparator_value (bool): the value of the custom keyword found in the schema file
            instance (dict): the properties object in the data file containing the _refine and _reflns categories
            schema (object): the json schema object
        returns:
            None
        raises:
            ValidationError: if the data file is invalid according to the schema
        """
        if not isinstance(instance, dict):
            return

        refine = instance.get("refine")
        reflns = instance.get("reflns")

        if refine is None or reflns is None:
            return

        if not isinstance(refine, list) or not isinstance(reflns, list):
            return

        if len(refine) != len(reflns):
            raise ValidationError(
                f"error, refine and reflns do not have the same length: {len(refine)} {len(reflns)}"
            )

        for index in range(len(refine)):
            item1 = refine[index]
            item2 = reflns[index]

            if not isinstance(item1, dict) or not isinstance(item2, dict):
                raise ValidationError(f"Items at index {index} must be objects")

            refine_high = float(item1.get("ls_d_res_high"))
            reflns_high = float(item2.get("d_resolution_high"))
            logger.debug(f"testing {refine_high} >= {reflns_high}")

            if refine_high is None or reflns_high is None:
                raise ValidationError(
                    f"error - wwpdb_resolution_comparator is missing a value at index {index}"
                )

            if refine_high != reflns_high or refine_high < reflns_high:
                raise ValidationError(
                    f"Mismatch in wwpdb_resolution_comparator at index {index} for refine {refine_high} reflns {reflns_high}"
                )

if __name__ == "__main__":
    print(Keywords.registry())
