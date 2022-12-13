# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import sys
import weakref
from unittest.mock import Mock

import pytest
import torch
from torch import nn
from torch.optim import Adam, SGD

from pytorch_lightning import LightningModule, Trainer
from pytorch_lightning.demos.boring_classes import BoringModel
from pytorch_lightning.loggers import TensorBoardLogger
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from pytorch_lightning.utilities.imports import _TORCH_GREATER_EQUAL_1_11, _TORCH_GREATER_EQUAL_1_13
from tests_pytorch.helpers.runif import RunIf


def test_lightning_module_not_abstract():
    """Test that the LightningModule can be instantiated (it is not an abstract class)."""
    _ = LightningModule()


def test_property_current_epoch():
    """Test that the current_epoch in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.current_epoch == 0

    trainer = Mock(current_epoch=123)
    model.trainer = trainer
    assert model.current_epoch == 123


def test_property_global_step():
    """Test that the global_step in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.global_step == 0

    trainer = Mock(global_step=123)
    model.trainer = trainer
    assert model.global_step == 123


def test_property_global_rank():
    """Test that the global rank in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.global_rank == 0

    trainer = Mock(global_rank=123)
    model.trainer = trainer
    assert model.global_rank == 123


def test_property_local_rank():
    """Test that the local rank in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.local_rank == 0

    trainer = Mock(local_rank=123)
    model.trainer = trainer
    assert model.local_rank == 123


def test_property_logger(tmpdir):
    """Test that the logger in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.logger is None

    logger = TensorBoardLogger(tmpdir)
    trainer = Trainer(logger=logger)
    model.trainer = trainer
    assert model.logger == logger


def test_property_loggers(tmpdir):
    """Test that loggers in LightningModule is accessible via the Trainer."""
    model = BoringModel()
    assert model.loggers == []

    logger = TensorBoardLogger(tmpdir)
    trainer = Trainer(logger=logger)
    model.trainer = trainer
    assert model.loggers == [logger]

    logger0 = TensorBoardLogger(tmpdir)
    logger1 = TensorBoardLogger(tmpdir)
    trainer = Trainer(logger=[logger0, logger1])
    model.trainer = trainer
    assert model.loggers == [logger0, logger1]


def test_1_optimizer_toggle_model():
    """Test toggle_model runs when only one optimizer is used."""
    model = BoringModel()
    trainer = Mock()
    model.trainer = trainer
    params = model.parameters()
    optimizer = torch.optim.SGD(params, lr=0.1)
    trainer.optimizers = [optimizer]

    assert not model._param_requires_grad_state
    # toggle optimizer was failing with a single optimizer
    model.toggle_optimizer(optimizer, 0)
    assert model._param_requires_grad_state
    model.untoggle_optimizer(0)
    assert not model._param_requires_grad_state


def test_toggle_untoggle_2_optimizers_no_shared_parameters(tmpdir):
    class TestModel(BoringModel):
        def training_step(self, batch, batch_idx, optimizer_idx=None):
            return super().training_step(batch, batch_idx)

        def __init__(self):
            super().__init__()
            self.layer_1 = nn.Sequential(nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32))

            self.layer_2 = nn.Sequential(
                nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 2)
            )

            # set some weights to False to check untoggle works as expected.
            self.layer_1[2].weight.requires_grad = False
            self.layer_1[4].weight.requires_grad = False

            self.layer_2[1].weight.requires_grad = False
            self.layer_2[3].weight.requires_grad = False

        def configure_optimizers(self):
            optimizer = SGD(self.layer_1.parameters(), lr=0.1)
            optimizer_2 = Adam(self.layer_2.parameters(), lr=0.1)
            return [optimizer, optimizer_2]

        def optimizer_step(
            self,
            current_epoch,
            batch_nb,
            optimizer,
            optimizer_idx,
            closure,
            on_tpu=False,
            using_lbfgs=False,
        ):
            if optimizer_idx == 0:
                assert self.layer_1[0].weight.requires_grad is True
                assert self.layer_1[2].weight.requires_grad is False
                assert self.layer_1[4].weight.requires_grad is False

                assert self.layer_2[1].weight.requires_grad is False
                assert self.layer_2[3].weight.requires_grad is False
                assert self.layer_2[5].weight.requires_grad is False

            if optimizer_idx == 1:
                assert self.layer_1[0].weight.requires_grad is False
                assert self.layer_1[2].weight.requires_grad is False
                assert self.layer_1[4].weight.requires_grad is False

                assert self.layer_2[1].weight.requires_grad is False
                assert self.layer_2[3].weight.requires_grad is False
                assert self.layer_2[5].weight.requires_grad is True

            optimizer.step(closure=closure)

    model = TestModel()
    model.training_epoch_end = None

    trainer = Trainer(
        max_epochs=1, default_root_dir=tmpdir, limit_train_batches=8, accumulate_grad_batches=2, limit_val_batches=0
    )
    trainer.fit(model)


def test_toggle_untoggle_3_optimizers_shared_parameters(tmpdir):
    class TestModel(BoringModel):
        def __init__(self):
            super().__init__()
            self.layer_1 = nn.Sequential(nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32))

            self.layer_2 = nn.Sequential(
                nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 2)
            )

            self.layer_3 = nn.Sequential(
                nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 2)
            )

            # set some weights to False to check untoggle works as expected.
            self.layer_1[2].weight.requires_grad = False
            self.layer_1[4].weight.requires_grad = False

            self.layer_2[1].weight.requires_grad = False
            self.layer_2[3].weight.requires_grad = False

            self.layer_3[1].weight.requires_grad = False
            self.layer_3[5].weight.requires_grad = False

        def optimizer_step(
            self,
            current_epoch,
            batch_nb,
            optimizer,
            optimizer_idx,
            closure,
            on_tpu=False,
            using_lbfgs=False,
        ):
            if optimizer_idx == 0:
                assert self.layer_1[0].weight.requires_grad is True
                assert self.layer_1[2].weight.requires_grad is False
                assert self.layer_1[4].weight.requires_grad is False

                assert self.layer_2[1].weight.requires_grad is False
                assert self.layer_2[3].weight.requires_grad is False
                assert self.layer_2[5].weight.requires_grad is True

                assert self.layer_3[1].weight.requires_grad is False
                assert self.layer_3[3].weight.requires_grad is False
                assert self.layer_3[5].weight.requires_grad is False

            if optimizer_idx == 1:
                assert self.layer_1[0].weight.requires_grad is False
                assert self.layer_1[2].weight.requires_grad is False
                assert self.layer_1[4].weight.requires_grad is False

                assert self.layer_2[1].weight.requires_grad is False
                assert self.layer_2[3].weight.requires_grad is False
                assert self.layer_2[5].weight.requires_grad is True

                assert self.layer_3[1].weight.requires_grad is False
                assert self.layer_3[3].weight.requires_grad is True
                assert self.layer_3[5].weight.requires_grad is False

            if optimizer_idx == 2:
                assert self.layer_1[0].weight.requires_grad is True
                assert self.layer_1[2].weight.requires_grad is False
                assert self.layer_1[4].weight.requires_grad is False

                assert self.layer_2[1].weight.requires_grad is False
                assert self.layer_2[3].weight.requires_grad is False
                assert self.layer_2[5].weight.requires_grad is False

                assert self.layer_3[1].weight.requires_grad is False
                assert self.layer_3[3].weight.requires_grad is True
                assert self.layer_3[5].weight.requires_grad is False

            optimizer.step(closure=closure)

        def training_step(self, batch, batch_idx, optimizer_idx=None):
            loss = super().training_step(batch, batch_idx)
            # make sure the model is untoggle when returning None
            return loss if batch_idx % 2 == 0 else None

        @staticmethod
        def combine_generators(gen_1, gen_2):
            yield from gen_1
            yield from gen_2

        def configure_optimizers(self):
            optimizer_1 = SGD(self.combine_generators(self.layer_1.parameters(), self.layer_2.parameters()), lr=0.1)
            optimizer_2 = Adam(self.combine_generators(self.layer_2.parameters(), self.layer_3.parameters()), lr=0.1)
            optimizer_3 = SGD(self.combine_generators(self.layer_3.parameters(), self.layer_1.parameters()), lr=0.1)
            return [optimizer_1, optimizer_2, optimizer_3]

    model = TestModel()
    model.training_epoch_end = None

    trainer = Trainer(max_epochs=1, default_root_dir=tmpdir, limit_train_batches=8, accumulate_grad_batches=2)

    trainer.fit(model)


@pytest.mark.parametrize(
    "accelerator,device",
    [
        pytest.param("gpu", "cuda:0", marks=RunIf(min_cuda_gpus=1)),
        pytest.param("mps", "mps:0", marks=RunIf(mps=True)),
    ],
)
def test_device_placement(tmpdir, accelerator, device):

    model = BoringModel()
    trainer = Trainer(default_root_dir=tmpdir, fast_dev_run=True, accelerator=accelerator, devices=1)
    trainer.fit(model)

    def assert_device(device: torch.device) -> None:
        assert model.device == device
        for p in model.parameters():
            assert p.device == device

    assert_device(torch.device("cpu"))
    model.to(torch.device(device))
    assert_device(torch.device(device))
    trainer.test(model)
    assert_device(torch.device("cpu"))
    trainer.predict(model, dataloaders=model.train_dataloader())
    assert_device(torch.device("cpu"))


@RunIf(skip_windows=True)
def test_sharded_tensor_state_dict(single_process_pg):
    if _TORCH_GREATER_EQUAL_1_11:
        from torch.distributed._shard.sharded_tensor import empty as sharded_tensor_empty
    else:
        from torch.distributed._sharded_tensor import empty as sharded_tensor_empty
    from torch.distributed._sharding_spec import ChunkShardingSpec

    class BoringModelWithShardedTensor(BoringModel):
        def __init__(self, spec):
            super().__init__()
            self.sharded_tensor = sharded_tensor_empty(spec, 10, 20)
            self.sharded_tensor.local_shards()[0].tensor.fill_(0)

    spec = ChunkShardingSpec(
        dim=0,
        placements=[
            "rank:0/cpu",
        ],
    )

    m_0 = BoringModelWithShardedTensor(spec)
    m_0.sharded_tensor.local_shards()[0].tensor.fill_(1)
    name_st = ".sharded_tensor" if _TORCH_GREATER_EQUAL_1_11 and not _TORCH_GREATER_EQUAL_1_13 else "sharded_tensor"
    assert name_st in m_0.state_dict(), 'Expect "sharded_tensor" to appear in the state dict'

    m_1 = BoringModelWithShardedTensor(spec)
    assert not torch.allclose(
        m_1.sharded_tensor.local_shards()[0].tensor, m_0.sharded_tensor.local_shards()[0].tensor
    ), "Expect the shards to be different before `m_1` loading `m_0`'s state dict"

    m_1.load_state_dict(m_0.state_dict(), strict=False)
    assert torch.allclose(
        m_1.sharded_tensor.local_shards()[0].tensor, m_0.sharded_tensor.local_shards()[0].tensor
    ), "Expect the shards to be same after `m_1` loading `m_0`'s state dict"


def test_lightning_module_configure_gradient_clipping(tmpdir):
    """Test custom gradient clipping inside `configure_gradient_clipping` hook."""

    class TestModel(BoringModel):

        has_validated_gradients = False
        custom_gradient_clip_val = 1e-2

        def configure_gradient_clipping(self, optimizer, optimizer_idx, gradient_clip_val, gradient_clip_algorithm):
            assert gradient_clip_val == self.trainer.gradient_clip_val
            assert gradient_clip_algorithm == self.trainer.gradient_clip_algorithm

            for pg in optimizer.param_groups:
                for p in pg["params"]:
                    p.grad.clamp_(min=0, max=self.custom_gradient_clip_val)

    model = TestModel()
    trainer = Trainer(
        default_root_dir=tmpdir, max_epochs=1, limit_train_batches=1, limit_val_batches=0, gradient_clip_val=1e-4
    )
    trainer.fit(model)

    optimizer = model.optimizers()
    for pg in optimizer.param_groups:
        for p in pg["params"]:
            if p.grad is not None:
                assert p.grad.min() >= 0
                assert p.grad.max() <= model.custom_gradient_clip_val


def test_lightning_module_configure_gradient_clipping_different_argument_values(tmpdir):
    """Test that setting gradient clipping arguments in `Trainer` and cusotmizing gradient clipping inside
    `configure_gradient_clipping` with different values raises an exception."""

    class TestModel(BoringModel):
        custom_gradient_clip_val = 1e-2

        def configure_gradient_clipping(self, optimizer, optimizer_idx, gradient_clip_val, gradient_clip_algorithm):
            self.clip_gradients(optimizer, gradient_clip_val=self.custom_gradient_clip_val)

    model = TestModel()
    trainer = Trainer(
        default_root_dir=tmpdir, max_epochs=1, limit_train_batches=2, limit_val_batches=0, gradient_clip_val=1e-4
    )
    with pytest.raises(
        MisconfigurationException,
        match=r"gradient_clip_val=0.0001\)` and have passed `clip_gradients\(gradient_clip_val=0.01",
    ):
        trainer.fit(model)

    class TestModel(BoringModel):
        custom_gradient_clip_algorithm = "foo"

        def configure_gradient_clipping(self, optimizer, optimizer_idx, gradient_clip_val, gradient_clip_algorithm):
            self.clip_gradients(optimizer, gradient_clip_algorithm=self.custom_gradient_clip_algorithm)

    model = TestModel()
    trainer = Trainer(
        default_root_dir=tmpdir,
        max_epochs=1,
        limit_train_batches=2,
        limit_val_batches=0,
        gradient_clip_algorithm="norm",
    )
    with pytest.raises(
        MisconfigurationException,
        match=r"gradient_clip_algorithm='norm'\)` and have passed `clip_gradients\(gradient_clip_algorithm='foo'",
    ):
        trainer.fit(model)


def test_proper_refcount():
    torch_module = nn.Module()
    lightning_module = LightningModule()

    assert sys.getrefcount(torch_module) == sys.getrefcount(lightning_module)


def test_lightning_module_scriptable():
    """Test that the LightningModule is `torch.jit.script`-able.

    Regression test for #15917.
    """
    model = BoringModel()
    trainer = Trainer()
    model.trainer = trainer
    torch.jit.script(model)


def test_trainer_reference_recursively():
    ensemble = LightningModule()
    inner = LightningModule()
    ensemble.inner = inner

    assert inner._trainer is None
    with pytest.raises(RuntimeError, match="attached to a `Trainer"):
        _ = ensemble.trainer

    trainer = Mock()
    ensemble.trainer = trainer
    # references match
    assert ensemble.trainer is inner.trainer
    # and the trainer was weakly referenced
    assert inner.trainer is weakref.proxy(trainer)


# TODO: replace with 1.14 when it is released
@RunIf(min_torch="1.14.0.dev20221202")
def test_compile_uncompile():

    lit_model = BoringModel()
    model_compiled = torch.compile(lit_model)

    lit_model_compiled = LightningModule.from_compiled(model_compiled)

    def has_dynamo(fn):
        return any(el for el in dir(fn) if el.startswith("_torchdynamo"))

    assert isinstance(lit_model_compiled, LightningModule)
    assert lit_model_compiled._compiler_ctx is not None
    assert has_dynamo(lit_model_compiled.forward)
    assert has_dynamo(lit_model_compiled.training_step)
    assert has_dynamo(lit_model_compiled.validation_step)
    assert has_dynamo(lit_model_compiled.test_step)
    assert has_dynamo(lit_model_compiled.predict_step)

    lit_model_orig = LightningModule.to_uncompiled(lit_model)

    assert lit_model_orig._compiler_ctx is None
    assert lit_model_orig.forward == lit_model.forward
    assert lit_model_orig.training_step == lit_model.training_step
    assert lit_model_orig.validation_step == lit_model.validation_step
    assert lit_model_orig.test_step == lit_model.test_step
    assert lit_model_orig.predict_step == lit_model.predict_step
    assert not has_dynamo(lit_model_orig.forward)
    assert not has_dynamo(lit_model_orig.training_step)
    assert not has_dynamo(lit_model_orig.validation_step)
    assert not has_dynamo(lit_model_orig.test_step)
    assert not has_dynamo(lit_model_orig.predict_step)
