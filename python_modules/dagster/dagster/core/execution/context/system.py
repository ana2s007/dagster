"""
This module contains the execution context objects that are internal to the system.
Not every property on these should be exposed to random Jane or Joe dagster user
so we have a different layer of objects that encode the explicit public API
in the user_context module
"""

from collections import namedtuple

from dagster import check
from dagster.core.definitions.hook import HookDefinition
from dagster.core.definitions.mode import ModeDefinition
from dagster.core.definitions.pipeline_base import IPipeline
from dagster.core.definitions.resource import ScopedResourcesBuilder
from dagster.core.definitions.solid import SolidDefinition
from dagster.core.definitions.step_launcher import StepLauncher
from dagster.core.errors import DagsterInvariantViolationError
from dagster.core.execution.plan.objects import StepOutputHandle
from dagster.core.execution.retries import Retries
from dagster.core.executor.base import Executor
from dagster.core.log_manager import DagsterLogManager
from dagster.core.storage.file_manager import FileManager
from dagster.core.storage.pipeline_run import PipelineRun
from dagster.core.system_config.objects import EnvironmentConfig


class SystemExecutionContextData(
    namedtuple(
        "_SystemExecutionContextData",
        (
            "pipeline_run scoped_resources_builder environment_config pipeline "
            "mode_def system_storage_def intermediate_storage_def instance intermediate_storage file_manager "
            "raise_on_error retries execution_plan"
        ),
    )
):
    """
    SystemExecutionContextData is the data that remains constant throughout the entire
    execution of a pipeline or plan.
    """

    def __new__(
        cls,
        pipeline_run,
        scoped_resources_builder,
        environment_config,
        pipeline,
        mode_def,
        system_storage_def,
        intermediate_storage_def,
        instance,
        intermediate_storage,
        file_manager,
        raise_on_error,
        retries,
        execution_plan,
    ):
        from dagster.core.definitions.system_storage import SystemStorageDefinition
        from dagster.core.definitions.intermediate_storage import IntermediateStorageDefinition
        from dagster.core.storage.intermediate_storage import IntermediateStorage
        from dagster.core.instance import DagsterInstance
        from dagster.core.execution.plan.plan import ExecutionPlan

        return super(SystemExecutionContextData, cls).__new__(
            cls,
            pipeline_run=check.inst_param(pipeline_run, "pipeline_run", PipelineRun),
            scoped_resources_builder=check.inst_param(
                scoped_resources_builder, "scoped_resources_builder", ScopedResourcesBuilder
            ),
            environment_config=check.inst_param(
                environment_config, "environment_config", EnvironmentConfig
            ),
            pipeline=check.inst_param(pipeline, "pipeline", IPipeline),
            mode_def=check.inst_param(mode_def, "mode_def", ModeDefinition),
            system_storage_def=check.inst_param(
                system_storage_def, "system_storage_def", SystemStorageDefinition
            ),
            intermediate_storage_def=check.opt_inst_param(
                intermediate_storage_def, "intermediate_storage_def", IntermediateStorageDefinition
            ),
            instance=check.inst_param(instance, "instance", DagsterInstance),
            intermediate_storage=check.inst_param(
                intermediate_storage, "intermediate_storage", IntermediateStorage
            ),
            file_manager=check.inst_param(file_manager, "file_manager", FileManager),
            raise_on_error=check.bool_param(raise_on_error, "raise_on_error"),
            retries=check.inst_param(retries, "retries", Retries),
            execution_plan=check.inst_param(execution_plan, "execution_plan", ExecutionPlan),
        )

    @property
    def run_id(self):
        return self.pipeline_run.run_id

    @property
    def run_config(self):
        return self.environment_config.original_config_dict

    @property
    def pipeline_def(self):
        return self.pipeline.get_definition()


class SystemExecutionContext:
    __slots__ = ["_execution_context_data", "_log_manager"]

    def __init__(self, execution_context_data, log_manager):
        self._execution_context_data = check.inst_param(
            execution_context_data, "execution_context_data", SystemExecutionContextData
        )
        self._log_manager = check.inst_param(log_manager, "log_manager", DagsterLogManager)

    @property
    def pipeline_run(self):
        return self._execution_context_data.pipeline_run

    @property
    def scoped_resources_builder(self):
        return self._execution_context_data.scoped_resources_builder

    @property
    def run_id(self):
        return self._execution_context_data.run_id

    @property
    def run_config(self):
        return self._execution_context_data.run_config

    @property
    def environment_config(self):
        return self._execution_context_data.environment_config

    @property
    def pipeline(self):
        return self._execution_context_data.pipeline

    @property
    def pipeline_def(self):
        return self._execution_context_data.pipeline_def

    @property
    def mode_def(self):
        return self._execution_context_data.mode_def

    @property
    def system_storage_def(self):
        return self._execution_context_data.system_storage_def

    @property
    def intermediate_storage_def(self):
        return self._execution_context_data.intermediate_storage_def

    @property
    def instance(self):
        return self._execution_context_data.instance

    @property
    def intermediate_storage(self):
        return self._execution_context_data.intermediate_storage

    @property
    def file_manager(self):
        return self._execution_context_data.file_manager

    @property
    def raise_on_error(self):
        return self._execution_context_data.raise_on_error

    @property
    def retries(self):
        return self._execution_context_data.retries

    @property
    def log(self):
        return self._log_manager

    @property
    def logging_tags(self):
        return self._log_manager.logging_tags

    @property
    def execution_plan(self):
        return self._execution_context_data.execution_plan

    def has_tag(self, key):
        check.str_param(key, "key")
        return key in self.logging_tags

    def get_tag(self, key):
        check.str_param(key, "key")
        return self.logging_tags.get(key)

    def for_step(self, step):
        from dagster.core.execution.plan.objects import ExecutionStep

        check.inst_param(step, "step", ExecutionStep)

        return SystemStepExecutionContext(
            self._execution_context_data, self._log_manager.with_tags(**step.logging_tags), step,
        )

    def for_type(self, dagster_type):
        return TypeCheckContext(self._execution_context_data, self.log, dagster_type)


class SystemPipelineExecutionContext(SystemExecutionContext):
    __slots__ = ["_executor"]

    def __init__(self, execution_context_data, log_manager, executor):
        super(SystemPipelineExecutionContext, self).__init__(execution_context_data, log_manager)
        self._executor = check.inst_param(executor, "executor", Executor)

    @property
    def executor(self):
        return self._executor


class SystemStepExecutionContext(SystemExecutionContext):
    __slots__ = ["_step", "_resources", "_required_resource_keys", "_step_launcher"]

    def __init__(self, execution_context_data, log_manager, step):
        from dagster.core.execution.plan.objects import ExecutionStep
        from dagster.core.execution.resources_init import get_required_resource_keys_for_step

        self._step = check.inst_param(step, "step", ExecutionStep)
        super(SystemStepExecutionContext, self).__init__(execution_context_data, log_manager)
        self._required_resource_keys = get_required_resource_keys_for_step(
            step,
            execution_context_data.execution_plan,
            execution_context_data.system_storage_def,
            execution_context_data.intermediate_storage_def,
        )
        self._resources = self._execution_context_data.scoped_resources_builder.build(
            self._required_resource_keys
        )
        step_launcher_resources = [
            resource for resource in self._resources if isinstance(resource, StepLauncher)
        ]
        if len(step_launcher_resources) > 1:
            raise DagsterInvariantViolationError(
                "Multiple required resources for solid {solid_name} have inherit StepLauncher"
                "There should be at most one step launcher resource per solid.".format(
                    solid_name=step.solid_handle.name
                )
            )
        elif len(step_launcher_resources) == 1:
            self._step_launcher = step_launcher_resources[0]
        else:
            self._step_launcher = None

        self._log_manager = log_manager

    def for_compute(self):
        return SystemComputeExecutionContext(self._execution_context_data, self.log, self.step)

    @property
    def step(self):
        return self._step

    @property
    def step_launcher(self):
        return self._step_launcher

    @property
    def solid_handle(self):
        return self._step.solid_handle

    @property
    def solid_def(self):
        return self.solid.definition

    @property
    def solid(self):
        return self.pipeline_def.get_solid(self._step.solid_handle)

    @property
    def resources(self):
        return self._resources

    @property
    def required_resource_keys(self):
        return self._required_resource_keys

    @property
    def log(self):
        return self._log_manager

    def for_hook(self, hook_def):
        return HookContext(self._execution_context_data, self.log, hook_def, self.step)

    def for_asset_store(self, step_output_handle, asset_store_handle):
        from dagster.core.storage.asset_store import AssetStoreHandle

        check.inst_param(step_output_handle, "step_output_handle", StepOutputHandle)
        check.inst_param(asset_store_handle, "asset_store_handle", AssetStoreHandle)

        # determine if the step is skipped
        if (
            # this is re-execution
            self.pipeline_run.parent_run_id
            # only part of the pipeline is being re-executed
            and self.pipeline_run.step_keys_to_execute
            # this step is not being executed
            and step_output_handle.step_key not in self.pipeline_run.step_keys_to_execute
        ):
            source_run_id = self.pipeline_run.parent_run_id
        else:
            source_run_id = self.pipeline_run.run_id

        return AssetStoreContext(
            step_key=step_output_handle.step_key,
            output_name=step_output_handle.output_name,
            asset_metadata=asset_store_handle.asset_metadata,
            pipeline_name=self.pipeline_def.name,
            solid_def=self.solid_def,
            source_run_id=source_run_id,
        )

    def get_asset_store(self, asset_store_key):
        from dagster.core.storage.asset_store import AssetStore

        # get AssetStore from resources using asset_store_key
        asset_store = getattr(self.resources, asset_store_key)
        return check.inst(asset_store, AssetStore)

    def using_asset_store(self, step_output_handle):
        # pylint: disable=comparison-with-callable
        from dagster.core.storage.asset_store import mem_asset_store

        asset_store_key = self.execution_plan.get_asset_store_key(step_output_handle)
        return self.mode_def.resource_defs[asset_store_key] != mem_asset_store


class SystemComputeExecutionContext(SystemStepExecutionContext):
    @property
    def solid_config(self):
        solid_config = self.environment_config.solids.get(str(self.solid_handle))
        return solid_config.config if solid_config else None


class TypeCheckContext(SystemExecutionContext):
    """The ``context`` object available to a type check function on a DagsterType.

    Attributes:
        log (DagsterLogManager): Centralized log dispatch from user code.
        resources (Any): An object whose attributes contain the resources available to this solid.
        run_id (str): The id of this pipeline run.
    """

    def __init__(self, execution_context_data, log_manager, dagster_type):
        super(TypeCheckContext, self).__init__(execution_context_data, log_manager)
        self._resources = self._execution_context_data.scoped_resources_builder.build(
            dagster_type.required_resource_keys
        )
        self._log_manager = log_manager

    @property
    def resources(self):
        return self._resources


class HookContext(SystemExecutionContext):
    """The ``context`` object available to a hook function on an DagsterEvent.

    Attributes:
        log (DagsterLogManager): Centralized log dispatch from user code.
        hook_def (HookDefinition): The hook that the context object belongs to.
        step (ExecutionStep): The compute step associated with the hook.
        solid (Solid): The solid instance associated with the hook.
        resources (Any): Resources available in the hook context.
        solid_config (Dict[str, Any]): The parsed config specific to this solid.
    """

    def __init__(self, execution_context_data, log_manager, hook_def, step):
        from dagster.core.execution.plan.objects import ExecutionStep

        super(HookContext, self).__init__(execution_context_data, log_manager)
        self._log_manager = log_manager
        self._hook_def = check.inst_param(hook_def, "hook_def", HookDefinition)
        self._step = check.inst_param(step, "step", ExecutionStep)

        self._required_resource_keys = hook_def.required_resource_keys
        self._resources = self._execution_context_data.scoped_resources_builder.build(
            self._required_resource_keys
        )

    @property
    def hook_def(self):
        return self._hook_def

    @property
    def step(self):
        return self._step

    @property
    def solid(self):
        return self.pipeline_def.get_solid(self._step.solid_handle)

    @property
    def resources(self):
        return self._resources

    @property
    def required_resource_keys(self):
        return self._required_resource_keys

    @property
    def solid_config(self):
        solid_config = self.environment_config.solids.get(str(self._step.solid_handle))
        return solid_config.config if solid_config else None


class AssetStoreContext(
    namedtuple(
        "_AssetStoreContext",
        "step_key output_name asset_metadata pipeline_name solid_def source_run_id",
    )
):
    """The ``context`` object available to :py:class:`AssetStore`.

    Attributes:
        step_key (str): The step_key for the compute step.
        output_name (str): The name of the output. (default: 'result').
        asset_metadata ([Dict[str, Any]]): A dict of the metadata that is used for the asset store
            to store or retrieve the data object.
        pipeline_name (str): The name of the pipeline.
        solid_def (SolidDefinition): The definition of the solid that uses the asset store.
        source_run_id (Optional[str]): The id of the run which generates the output.
    """

    def __new__(
        cls, step_key, output_name, asset_metadata, pipeline_name, solid_def, source_run_id=None
    ):

        return super(AssetStoreContext, cls).__new__(
            cls,
            step_key=check.str_param(step_key, "step_key"),
            output_name=check.str_param(output_name, "output_name"),
            asset_metadata=check.opt_dict_param(asset_metadata, "asset_metadata", key_type=str),
            pipeline_name=check.str_param(pipeline_name, "pipeline_name"),
            solid_def=check.inst_param(solid_def, "solid_def", SolidDefinition),
            source_run_id=check.opt_str_param(source_run_id, "source_run_id"),
        )

    def get_run_scoped_output_identifier(self):
        """Utility method to get a collection of identifiers that as a whole represent a unique
        step output.

        The unique identifier collection consists of

        - ``source_run_id``: the id of the run which generates the output.
            Note: This method also handles the re-execution memoization logic. If the step that
            generates the output is skipped in the re-execution, the ``run_id`` will be the id
            of its parent run.
        - ``step_key``: the key for a compute step.
        - ``output_name``: the name of the output. (default: 'result').

        Returns:
            List[str, ...]: A list of identifiers, i.e. run id, step key, and output name
        """
        return [self.source_run_id, self.step_key, self.output_name]
