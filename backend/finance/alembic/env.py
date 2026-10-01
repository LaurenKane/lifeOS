"""alembic env.py for finance schema."""

from __future__ import annotations

from logging.config import fileConfig

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
from alembic.config import Config

config = Config(file_name="alembic.ini")

# Interpret the config for this script.
# This will set up logging etc.
if config is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here for 'autogenerate support'
# from finance.domain.models import Base
# target_metadata = Base.metadata
target_metadata = None

# other values from the config, defined in the .ini file:
# my_important_option = config.get_main_option("my_important_option")
