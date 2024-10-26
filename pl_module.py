import torch
from pytorch_lightning import LightningModule
from torch.optim import Adam
from torch.optim.lr_scheduler import CosineAnnealingLR
from abc import abstractmethod
from log_tools.logger import (
    LoggerCollection,
    BaseMetricsLogger,
    ScalarLogger,
    MetricsTableLogger,
    DictLogger,
    ConfusionMatrixLogger,
    PerClassLogger,
)
from log_tools.metrics_manager import (
    MetricsManager,
    MetricsManagerCollection,
    BaseMetricsManager,
)


class BaseLightningModule(LightningModule):
    def __init__(
        self,
        model,
        model_name,
        loss_fn,
        metrics,
        lr,
        scheduler_max_it,
        weight_decay=0,
    ):
        super().__init__()
        self.model = model
        self.model_name = model_name
        self.loss_fn = loss_fn
        self.scheduler_max_it = scheduler_max_it
        self.weight_decay = weight_decay
        self.lr = lr

        self.train_metrics = metrics.clone(prefix="train/")
        self.val_metrics = metrics.clone(prefix="val/")
        self.test_metrics = metrics.clone(prefix="test/")

        self.train_metrics_manager: BaseMetricsManager
        self.val_metrics_manager: BaseMetricsManager
        self.test_metrics_manager: BaseMetricsManager

        self.train_loss_logger: BaseMetricsLogger
        self.val_loss_logger: BaseMetricsLogger
        self.test_loss_logger: BaseMetricsLogger

        self.train_metrics_logger: BaseMetricsLogger
        self.val_metrics_logger: BaseMetricsLogger
        self.test_metrics_logger: BaseMetricsLogger

    @abstractmethod
    def configure_metrics_managers(self):
        """Configure metrics managers."""
        pass

    @abstractmethod
    def configure_loggers(self):
        """Configure loggers."""
        pass

    def forward(self, X):
        """Forward pass of the Convolutional model.

        Args:
        ----
            X: Input tensor.

        Returns:
        -------
            Output tensor.

        """
        outputs = self.model(X)
        return outputs

    def _final_step(self, y_hat):
        """Final step of the forward pass. Includes the final activation function if any and the final prediction.
        This should be overriden by the subclass if needed (for example for classification tasks).

        Args:
        ----
            y_hat: Predicted outputs.

        Returns:
        -------
            Predicted outputs.

        """
        return y_hat

    def _common_step(self, batch, batch_idx):
        """Common step for training, validation, and testing.

        Args:
        ----
            batch: Input batch.
            batch_idx: Index of the current batch.

        Returns:
        -------
            Tuple containing the ground truth labels, predicted outputs, and loss value.

        """
        x, y = batch
        y_hat = self(x)

        loss = self.loss_fn(y_hat[0], y)
        return y, y_hat, loss

    def training_step(self, batch, batch_idx):
        """Training step.

        Args:
        ----
            batch: Input batch.
            batch_idx: Index of the current batch.

        Returns:
        -------
            Dictionary containing the loss value, ground truth labels, and predicted outputs.

        """
        y, y_hat, loss = self._common_step(batch, batch_idx)
        y_hat = self._final_step(y_hat)

        self.train_metrics_manager.update_metrics(y_true=y, y_pred=y_hat)

        self.train_loss_logger.log(loss)
        return {"loss": loss, "train/labels": y, "train/predictions": y_hat}

    def on_train_epoch_end(self):
        """Callback function called at the end of each training epoch.
        Computes and logs the training metrics.
        """
        self.train_metrics_logger.log()
        self.train_metrics_manager.reset_metrics()

    def validation_step(self, batch, batch_idx):
        """Validation step.

        Args:
        ----
            batch: Input batch.
            batch_idx: Index of the current batch.

        Returns:
        -------
            Dictionary containing the loss value, ground truth labels, and predicted outputs.

        """
        y, y_hat, loss = self._common_step(batch, batch_idx)
        y_hat = self._final_step(y_hat)

        self.val_metrics_manager.update_metrics(y_true=y, y_pred=y_hat)

        self.val_loss_logger.log(loss)
        return {"loss": loss, "val/labels": y, "val/predictions": y_hat}

    def on_validation_epoch_end(self):
        """Callback function called at the end of each validation epoch.
        Computes and logs the validation metrics.
        """
        self.val_metrics_logger.log()
        self.val_metrics_manager.reset_metrics()

    def test_step(self, batch, batch_idx):
        """Test step.

        Args:
        ----
            batch: Input batch.
            batch_idx: Index of the current batch.

        Returns:
        -------
            Dictionary containing the loss value, ground truth labels, and predicted outputs.

        """
        y, y_hat, loss = self._common_step(batch, batch_idx)
        y_hat = self._final_step(y_hat)

        self.test_metrics_manager.update_metrics(y_true=y, y_pred=y_hat)

        self.test_loss_logger.log(loss)
        return {"loss": loss, "test/labels": y, "test/predictions": y_hat}

    def on_test_epoch_end(self):
        """Callback function called at the end of each testing epoch.
        Computes and logs the testing metrics.
        """
        self.test_metrics_logger.log()
        self.test_metrics_manager.reset_metrics()

    def configure_optimizers(self):
        """Configure the optimizer and learning rate scheduler.

        Returns
        -------
            Tuple containing the optimizer and learning rate scheduler.

        """
        optimizer = Adam(self.model.parameters(), lr=self.lr)
        scheduler = CosineAnnealingLR(optimizer, T_max=self.scheduler_max_it)
        return [optimizer], [scheduler]


class ClassificationLightningModule(BaseLightningModule):
    def __init__(
        self,
        model,
        model_name,
        class_names,
        loss_fn,
        metrics,
        lr,
        scheduler_max_it,
        weight_decay=0,
        per_class_metrics=None,
    ):
        super().__init__(
            model=model,
            model_name=model_name,
            loss_fn=loss_fn,
            metrics=metrics,
            lr=lr,
            scheduler_max_it=scheduler_max_it,
            weight_decay=weight_decay,
        )

        self.class_names = class_names
        self.per_class_metrics = per_class_metrics
        self.test_all_preds = []
        self.test_all_targets = []

        self.configure_metrics_managers()
        self.configure_loggers()

    def _final_step(self, y_hat):
        return torch.argmax(torch.softmax(y_hat[0], dim=-1), dim=-1)

    def configure_metrics_managers(self):
        self.train_metrics_manager = MetricsManager(
            module=self,
            metrics=self.train_metrics,
        )
        self.val_metrics_manager = MetricsManager(
            module=self,
            metrics=self.val_metrics,
        )

        if self.per_class_metrics is not None:
            self.test_metrics_manager = MetricsManagerCollection(
                module=self,
                managers=[
                    MetricsManager(module=self, metrics=self.test_metrics),
                    MetricsManager(module=self, metrics=self.per_class_metrics),
                ],
            )
        else:
            self.test_metrics_manager = MetricsManager(
                module=self, metrics=self.test_metrics
            )

    def configure_loggers(self):
        self.train_loss_logger = ScalarLogger(
            prefix="train/",
            module=self,
            scalar_name="loss",
        )
        self.val_loss_logger = ScalarLogger(
            prefix="val/",
            module=self,
            scalar_name="loss",
        )
        self.test_loss_logger = ScalarLogger(
            prefix="test/",
            module=self,
            scalar_name="loss",
        )

        self.train_metrics_logger = DictLogger(
            prefix="train/", module=self, metrics=self.train_metrics
        )
        self.val_metrics_logger = DictLogger(
            prefix="val/", module=self, metrics=self.val_metrics
        )

        if self.per_class_metrics is not None:
            self.test_metrics_logger = LoggerCollection(
                prefix="test/",
                module=self,
                loggers=[
                    DictLogger(
                        prefix="test/", module=self, metrics=self.test_metrics
                    ),
                    MetricsTableLogger(
                        prefix="test/",
                        module=self,
                        metrics=self.test_metrics,
                        table_name="Metrics",
                    ),
                    PerClassLogger(
                        prefix="test/", module=self, metrics=self.per_class_metrics
                    ),
                    ConfusionMatrixLogger(
                        prefix="test/",
                        module=self,
                        y_true_ref=self.test_all_targets,
                        y_pred_ref=self.test_all_preds,
                    ),
                ],
            )
        else:
            self.test_metrics_logger = LoggerCollection(
                prefix="test/",
                module=self,
                loggers=[
                    DictLogger(
                        prefix="test/", module=self, metrics=self.test_metrics
                    ),
                    MetricsTableLogger(
                        prefix="test/",
                        module=self,
                        metrics=self.test_metrics,
                        table_name="Metrics",
                    ),
                    ConfusionMatrixLogger(prefix="test/", module=self),
                ],
            )

    def test_step(self, batch, batch_idx):
        test_results = super().test_step(batch, batch_idx)

        self.test_all_preds.append(test_results["test/predictions"])
        self.test_all_targets.append(test_results["test/labels"])

        return test_results

    def on_test_epoch_end(self):
        super().on_test_epoch_end()

        self.test_all_preds = []
        self.test_all_targets = []
