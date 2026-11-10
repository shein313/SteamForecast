#!/usr/bin/env Rscript
# =============================================================================
# Steam Forecast: обучение моделей регрессии (lm и Random Forest)
# -----------------------------------------------------------------------------
# Вход : data/processed/games_clean.csv
# Цель : y = log1p(total_reviews)
# Выход: models/model_rf.rds                    - лучшая модель (список-«пакет», см. ниже)
#        reports/figures/feature_importance.png - важность признаков Random Forest
#        reports/model_metrics.csv              - метрики R2 / MAE / RMSE на тесте
#
# Содержимое models/model_rf.rds (list):
#   model         - лучшая по R2 модель (lm или randomForest)
#   model_type    - "lm" | "randomForest"
#   features      - порядок признаков, ожидаемый моделью
#   medians       - медианы train для заполнения пропусков
#   ranges        - диапазоны числовых признаков (для слайдеров в Shiny)
#   importance    - data.frame(feature, importance) из Random Forest (%IncMSE)
#   metrics       - data.frame с метриками обеих моделей
#
# Запуск:  Rscript src/train_models.R
# =============================================================================

# ---- 0. Пакеты ---------------------------------------------------------------
for (pkg in c("randomForest")) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    install.packages(pkg, repos = "https://cloud.r-project.org")
  }
}
suppressPackageStartupMessages(library(randomForest))

# ---- 1. Пути -----------------------------------------------------------------
find_project_root <- function() {
  dir <- normalizePath(getwd(), winslash = "/")
  repeat {
    if (file.exists(file.path(dir, "data", "processed", "games_clean.csv"))) return(dir)
    parent <- dirname(dir)
    if (identical(parent, dir)) stop("Не найден корень проекта (data/processed/games_clean.csv).")
    dir <- parent
  }
}

PROJECT_ROOT <- find_project_root()
DATA_PATH    <- file.path(PROJECT_ROOT, "data", "processed", "games_clean.csv")
MODEL_DIR    <- file.path(PROJECT_ROOT, "models")
FIG_DIR      <- file.path(PROJECT_ROOT, "reports", "figures")
REPORT_DIR   <- file.path(PROJECT_ROOT, "reports")
for (d in c(MODEL_DIR, FIG_DIR)) dir.create(d, recursive = TRUE, showWarnings = FALSE)

SEED       <- 42
TRAIN_FRAC <- 0.8
set.seed(SEED)

# ---- 2. Данные и признаки ----------------------------------------------------
games <- read.csv(DATA_PATH, stringsAsFactors = FALSE, encoding = "UTF-8")
games$log_reviews <- log1p(games$total_reviews)

# positive_ratio и reviews_per_year вычисляются из отзывов -> утечка таргета,
# поэтому в признаки не включаем (как и в Python-baseline).
FEATURES <- c("price", "release_year", "languages_count",
              "is_multiplayer", "is_singleplayer", "is_coop",
              "genre_action", "genre_rpg", "genre_strategy",
              "genre_adventure", "genre_indie", "genre_simulation")
FEATURES <- intersect(FEATURES, names(games))

X <- games[, FEATURES, drop = FALSE]
X[] <- lapply(X, as.numeric)
y <- games$log_reviews
cat(sprintf("Строк: %d, признаков: %d\n", nrow(X), ncol(X)))

# ---- 3. Разбиение Train 80% / Test 20% --------------------------------------
n         <- nrow(X)
train_idx <- sample(seq_len(n), size = floor(TRAIN_FRAC * n))
X_train <- X[train_idx, , drop = FALSE];  y_train <- y[train_idx]
X_test  <- X[-train_idx, , drop = FALSE]; y_test  <- y[-train_idx]
cat(sprintf("Train: %d, Test: %d\n", nrow(X_train), nrow(X_test)))

# Пропуски (напр. release_year) заполняем медианой ОБУЧАЮЩЕЙ выборки
medians <- sapply(X_train, median, na.rm = TRUE)
impute <- function(df, med) {
  for (col in names(med)) df[[col]][is.na(df[[col]])] <- med[[col]]
  df
}
X_train <- impute(X_train, medians)
X_test  <- impute(X_test,  medians)

# ---- 4. Метрики --------------------------------------------------------------
r2   <- function(y, p) 1 - sum((y - p)^2) / sum((y - mean(y))^2)
mae  <- function(y, p) mean(abs(y - p))
rmse <- function(y, p) sqrt(mean((y - p)^2))

evaluate <- function(name, pred, truth) {
  data.frame(model = name, R2 = r2(truth, pred),
             MAE = mae(truth, pred), RMSE = rmse(truth, pred))
}

# ---- 5. Обучение моделей -----------------------------------------------------
train_df <- cbind(X_train, log_reviews = y_train)

# 5.1 Линейная регрессия
lm_model <- lm(log_reviews ~ ., data = train_df)
lm_pred  <- predict(lm_model, newdata = X_test)

# 5.2 Random Forest (importance = TRUE -> %IncMSE и IncNodePurity)
set.seed(SEED)
rf_model <- randomForest(x = X_train, y = y_train,
                         ntree = 500, importance = TRUE)
rf_pred  <- predict(rf_model, newdata = X_test)

metrics <- rbind(
  evaluate("Linear Regression (lm)", lm_pred, y_test),
  evaluate("Random Forest",           rf_pred, y_test)
)
cat("\nМетрики на тестовой выборке (цель: log1p(total_reviews))\n")
print(format(metrics, digits = 4), row.names = FALSE)
write.csv(metrics, file.path(REPORT_DIR, "model_metrics.csv"),
          row.names = FALSE, fileEncoding = "UTF-8")

# Результаты lm для справки
cat("\nСводка линейной регрессии:\n")
print(summary(lm_model))

# ---- 6. Важность признаков Random Forest ------------------------------------
imp_mat <- importance(rf_model)  # столбцы: %IncMSE, IncNodePurity
imp_df  <- data.frame(feature    = rownames(imp_mat),
                      importance = imp_mat[, "%IncMSE"],
                      row.names  = NULL)
imp_df  <- imp_df[order(imp_df$importance, decreasing = TRUE), ]
cat("\nВажность признаков (%IncMSE):\n")
print(imp_df, row.names = FALSE)

png(file.path(FIG_DIR, "feature_importance.png"), width = 1000, height = 700, res = 120)
varImpPlot(rf_model, type = 1, main = "Важность признаков (Random Forest, %IncMSE)",
           pch = 19, col = "#2a475e")
invisible(dev.off())

# ---- 7. Выбор и сохранение лучшей модели ------------------------------------
best_is_rf <- metrics$R2[metrics$model == "Random Forest"] >=
              metrics$R2[metrics$model == "Linear Regression (lm)"]
best_model <- if (best_is_rf) rf_model else lm_model
best_type  <- if (best_is_rf) "randomForest" else "lm"

# Диапазоны признаков - используются для настройки слайдеров приложения
ranges <- lapply(X[, c("price", "release_year", "languages_count")],
                 range, na.rm = TRUE)

bundle <- list(
  model      = best_model,
  model_type = best_type,
  features   = FEATURES,
  medians    = medians,
  ranges     = ranges,
  importance = imp_df,
  metrics    = metrics
)
saveRDS(bundle, file.path(MODEL_DIR, "model_rf.rds"))
cat(sprintf("\nЛучшая модель по R2: %s. Сохранена в %s\n",
            best_type, file.path(MODEL_DIR, "model_rf.rds")))
cat("График важности:", file.path(FIG_DIR, "feature_importance.png"), "\n")
