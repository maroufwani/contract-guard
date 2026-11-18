"""SpecError: raised for specs that cannot be loaded, validated or normalized."""


class SpecError(ValueError):
    """A spec is invalid or unsupported; message is shown to the uploader."""
