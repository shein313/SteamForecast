#!/usr/bin/env Rscript
# =============================================================================
# Steam Forecast: разведочный анализ и проверка статистических гипотез
# -----------------------------------------------------------------------------
# Вход : data/processed/games_clean.csv
# Выход: reports/figures/hist_reviews.png
#        reports/figures/box_multiplayer.png
#        reports/figures/corr_matrix.png
#        reports/figures/box_price_genre.png   (иллюстрация к гипотезе 3)
#        reports/hypotheses_summary.csv        (сводная таблица результатов)
#
# Запуск (из любой папки проекта):  Rscript src/analysis_and_hypotheses.R
# Уровень значимости для всех гипотез: alpha = 0.05
# =============================================================================

# ---- 0. Пакеты ---------------------------------------------------------------
required_pkgs <- c("ggplot2")
for (pkg in required_pkgs) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    install.packages(pkg, repos = "https://cloud.r-project.org")
  }
}
suppressPackageStartupMessages(library(ggplot2))

# ---- 1. Пути -----------------------------------------------------------------
# Корень проекта ищем, поднимаясь вверх от рабочей папки до каталога,
# содержащего data/processed/games_clean.csv (работает и из корня, и из src/).
find_project_root <- function() {
  dir <- normalizePath(getwd(), winslash = "/")
  repeat {
    if (file.exists(file.path(dir, "data", "processed", "games_clean.csv"))) {
      return(dir)
    }
    parent <- dirname(dir)
    if (identical(parent, dir)) stop("Не найден корень проекта (data/processed/games_clean.csv).")
    dir <- parent
  }
}

PROJECT_ROOT <- find_project_root()
DATA_PATH    <- file.path(PROJECT_ROOT, "data", "processed", "games_clean.csv")
FIG_DIR      <- file.path(PROJECT_ROOT, "reports", "figures")
REPORT_DIR   <- file.path(PROJECT_ROOT, "reports")
dir.create(FIG_DIR, recursive = TRUE, showWarnings = FALSE)

ALPHA <- 0.05
set.seed(42)

# ---- 2. Загрузка и подготовка данных ----------------------------------------
games <- read.csv(DATA_PATH, stringsAsFactors = FALSE, encoding = "UTF-8")
cat(sprintf("Загружено строк: %d, столбцов: %d\n", nrow(games), ncol(games)))

# Логарифм числа отзывов: сглаживает сильно скошенное распределение
games$log_reviews <- log1p(games$total_reviews)

# Бесплатность: берём is_free, если столбец есть, иначе price == 0
games$is_free <- if ("is_free" %in% names(games)) {
  as.integer(games$is_free)
} else {
  as.integer(games$price == 0)
}

# Подписанные факторы для таблиц и графиков
games$multiplayer_f <- factor(games$is_multiplayer, levels = c(0, 1),
                              labels = c("Без мультиплеера", "С мультиплеером"))
games$free_f        <- factor(games$is_free, levels = c(0, 1),
                              labels = c("Платная", "Бесплатная"))

# Вспомогательная функция: красивый вывод результата гипотезы
print_hypothesis <- function(title, h0, h1, test_name, statistic, p_value, note = NULL) {
  cat("\n", strrep("=", 78), "\n", title, "\n", strrep("=", 78), "\n", sep = "")
  cat("H0:", h0, "\n")
  cat("H1:", h1, "\n")
  cat("Критерий:", test_name, "\n")
  cat("Статистика:", statistic, "\n")
  cat(sprintf("p-value = %.6g (alpha = %.2f)\n", p_value, ALPHA))
  if (p_value < ALPHA) {
    cat("Вывод: p-value < alpha -> H0 отвергается, различия статистически значимы.\n")
  } else {
    cat("Вывод: p-value >= alpha -> оснований отвергнуть H0 нет.\n")
  }
  if (!is.null(note)) cat("Примечание:", note, "\n")
}

summary_rows <- list()

# ---- 3. Гипотеза 1: Манн-Уитни (мультиплеер -> log1p(отзывы)) ---------------
# Распределение log_reviews не нормально (много нулей), группы независимы,
# поэтому применяем непараметрический U-критерий Манна-Уитни.
shapiro_check <- tapply(games$log_reviews, games$is_multiplayer,
                        function(x) shapiro.test(sample(x, min(length(x), 5000)))$p.value)
cat("\nShapiro-Wilk p-value для log_reviews по группам is_multiplayer:\n")
print(shapiro_check)

# exact = FALSE: в данных много совпадающих значений (ties)
w1 <- wilcox.test(log_reviews ~ is_multiplayer, data = games,
                  exact = FALSE, conf.int = TRUE)

n_mp   <- sum(games$is_multiplayer == 1)
n_nomp <- sum(games$is_multiplayer == 0)
# Ранговая бисериальная корреляция - размер эффекта: r = 2U/(n1*n2) - 1
rank_biserial <- 2 * unname(w1$statistic) / (n_mp * n_nomp) - 1

print_hypothesis(
  title     = "ГИПОТЕЗА 1: влияние мультиплеера на log1p(total_reviews)",
  h0        = "распределения log1p(total_reviews) у игр с мультиплеером и без него совпадают.",
  h1        = "распределения различаются (сдвиг медиан не равен нулю).",
  test_name = "U-критерий Манна-Уитни (wilcox.test, двусторонний)",
  statistic = sprintf("W = %.1f; n(без) = %d, n(с) = %d; ранг.-бисер. r = %.3f",
                      unname(w1$statistic), n_nomp, n_mp, rank_biserial),
  p_value   = w1$p.value
)
cat("Медианы log_reviews по группам:\n")
print(tapply(games$log_reviews, games$multiplayer_f, median))

summary_rows[[1]] <- data.frame(
  hypothesis = "H1: мультиплеер -> log1p(reviews)", test = "Mann-Whitney (wilcox.test)",
  statistic = unname(w1$statistic), p_value = w1$p.value,
  decision = ifelse(w1$p.value < ALPHA, "H0 отвергается", "H0 не отвергается")
)

# ---- 4. Гипотеза 2: хи-квадрат Пирсона (бесплатность vs мультиплеер) --------
tab2 <- table(Мультиплеер = games$multiplayer_f, Бесплатность = games$free_f)
cat("\nТаблица сопряжённости:\n"); print(tab2)
chi2 <- chisq.test(tab2)  # по умолчанию - поправка Йейтса для таблицы 2x2
cat("\nОжидаемые частоты:\n"); print(round(chi2$expected, 2))

note2 <- NULL
if (any(chi2$expected < 5)) {
  note2 <- "ожидаемые частоты < 5, результат хи-квадрат приблизителен (см. также точный тест Фишера)."
  cat("Точный тест Фишера: p-value =", signif(fisher.test(tab2)$p.value, 6), "\n")
}
# Размер эффекта: V Крамера
cramers_v <- sqrt(unname(chi2$statistic) / (sum(tab2) * (min(dim(tab2)) - 1)))

print_hypothesis(
  title     = "ГИПОТЕЗА 2: связь бесплатности игры и наличия мультиплеера",
  h0        = "бесплатность (price == 0) и наличие мультиплеера независимы.",
  h1        = "между бесплатностью и наличием мультиплеера есть связь.",
  test_name = "Хи-квадрат Пирсона (chisq.test, поправка Йейтса)",
  statistic = sprintf("X-squared = %.4f, df = %d; V Крамера = %.3f",
                      unname(chi2$statistic), unname(chi2$parameter), cramers_v),
  p_value   = chi2$p.value,
  note      = note2
)
cat("Доля бесплатных игр по группам мультиплеера:\n")
print(round(prop.table(tab2, margin = 1), 3))

summary_rows[[2]] <- data.frame(
  hypothesis = "H2: бесплатность <-> мультиплеер", test = "Chi-squared (chisq.test)",
  statistic = unname(chi2$statistic), p_value = chi2$p.value,
  decision = ifelse(chi2$p.value < ALPHA, "H0 отвергается", "H0 не отвергается")
)

# ---- 5. Гипотеза 3: цена по жанрам Action / RPG / Strategy ------------------
# Жанры в данных - бинарные флаги (игра может иметь несколько жанров).
# Для независимости групп берём игры ровно из ОДНОГО из трёх жанров.
genre_flags <- games[, c("genre_action", "genre_rpg", "genre_strategy")]
only_one    <- rowSums(genre_flags) == 1
g3 <- games[only_one, ]
g3$genre <- factor(
  c("Action", "RPG", "Strategy")[max.col(g3[, c("genre_action", "genre_rpg", "genre_strategy")],
                                         ties.method = "first")],
  levels = c("Action", "RPG", "Strategy")
)
cat("\nИгр, относящихся ровно к одному из жанров Action/RPG/Strategy:", nrow(g3), "\n")
print(table(g3$genre))
cat("Средняя и медианная цена по жанрам:\n")
print(aggregate(price ~ genre, data = g3,
                FUN = function(x) c(mean = mean(x), median = median(x))))

# 5.1 Параметрический вариант: однофакторный ANOVA
aov_fit <- aov(price ~ genre, data = g3)
aov_tab <- summary(aov_fit)[[1]]
cat("\nANOVA:\n"); print(summary(aov_fit))
shapiro_res <- shapiro.test(residuals(aov_fit)[seq_len(min(5000, length(residuals(aov_fit))))])
cat(sprintf("Shapiro-Wilk остатков ANOVA: p = %.4g (нормальность %s)\n",
            shapiro_res$p.value, ifelse(shapiro_res$p.value < ALPHA, "нарушена", "не отвергается")))

# 5.2 Непараметрический вариант: Краскел-Уоллис (основной, цены не нормальны)
kw <- kruskal.test(price ~ genre, data = g3)

print_hypothesis(
  title     = "ГИПОТЕЗА 3: различие цены между жанрами Action, RPG, Strategy",
  h0        = "распределения цены в жанрах Action, RPG и Strategy совпадают (средние/медианы равны).",
  h1        = "хотя бы один жанр отличается по цене от остальных.",
  test_name = "Краскел-Уоллис (kruskal.test); для сравнения - ANOVA (aov)",
  statistic = sprintf("Kruskal-Wallis chi-squared = %.4f, df = %d; ANOVA F = %.4f (p = %.6g)",
                      unname(kw$statistic), unname(kw$parameter),
                      aov_tab[["F value"]][1], aov_tab[["Pr(>F)"]][1]),
  p_value   = kw$p.value
)

if (kw$p.value < ALPHA) {
  cat("\nПопарные сравнения (Wilcoxon, поправка Холма):\n")
  print(pairwise.wilcox.test(g3$price, g3$genre, p.adjust.method = "holm", exact = FALSE))
}

summary_rows[[3]] <- data.frame(
  hypothesis = "H3: цена ~ жанр (Action/RPG/Strategy)", test = "Kruskal-Wallis (kruskal.test)",
  statistic = unname(kw$statistic), p_value = kw$p.value,
  decision = ifelse(kw$p.value < ALPHA, "H0 отвергается", "H0 не отвергается")
)

# Сводная таблица результатов
summary_df <- do.call(rbind, summary_rows)
write.csv(summary_df, file.path(REPORT_DIR, "hypotheses_summary.csv"),
          row.names = FALSE, fileEncoding = "UTF-8")
cat("\nСводка по гипотезам:\n"); print(summary_df, row.names = FALSE)

# ---- 6. Графики --------------------------------------------------------------
theme_set(theme_minimal(base_size = 13))

# 6.1 Гистограмма логарифма отзывов
p_hist <- ggplot(games, aes(x = log_reviews)) +
  geom_histogram(bins = 30, fill = "#2a475e", colour = "white") +
  labs(title = "Распределение log1p(total_reviews)",
       x = "log(1 + число отзывов)", y = "Количество игр")
ggsave(file.path(FIG_DIR, "hist_reviews.png"), p_hist, width = 8, height = 5, dpi = 150)

# 6.2 Boxplot отзывов по наличию мультиплеера
p_box <- ggplot(games, aes(x = multiplayer_f, y = log_reviews, fill = multiplayer_f)) +
  geom_boxplot(show.legend = FALSE, outlier.alpha = 0.4) +
  scale_fill_manual(values = c("#8f98a0", "#66c0f4")) +
  labs(title = "Отзывы в зависимости от мультиплеера",
       subtitle = sprintf("Манн-Уитни: p = %.4g", w1$p.value),
       x = NULL, y = "log(1 + число отзывов)")
ggsave(file.path(FIG_DIR, "box_multiplayer.png"), p_box, width = 7, height = 5, dpi = 150)

# 6.3 Матрица корреляций числовых признаков (Спирмен - устойчив к выбросам)
num_cols <- c("log_reviews", "positive_ratio", "reviews_per_year", "price",
              "release_year", "languages_count", "is_multiplayer", "is_singleplayer",
              "is_coop", grep("^genre_", names(games), value = TRUE))
num_cols <- intersect(num_cols, names(games))
cor_mat  <- cor(games[, num_cols], use = "pairwise.complete.obs", method = "spearman")
cor_long <- as.data.frame(as.table(cor_mat))
names(cor_long) <- c("var1", "var2", "r")

p_corr <- ggplot(cor_long, aes(var1, var2, fill = r)) +
  geom_tile(colour = "white") +
  geom_text(aes(label = sprintf("%.2f", r)), size = 2.8) +
  scale_fill_gradient2(low = "#c0392b", mid = "white", high = "#2980b9",
                       midpoint = 0, limits = c(-1, 1), name = "Spearman") +
  labs(title = "Матрица корреляций числовых признаков", x = NULL, y = NULL) +
  theme(axis.text.x = element_text(angle = 45, hjust = 1))
ggsave(file.path(FIG_DIR, "corr_matrix.png"), p_corr, width = 10, height = 8, dpi = 150)

# 6.4 Дополнительно: цена по жанрам (иллюстрация к гипотезе 3)
p_genre <- ggplot(g3, aes(x = genre, y = price, fill = genre)) +
  geom_boxplot(show.legend = FALSE, outlier.alpha = 0.4) +
  labs(title = "Цена игр по жанрам",
       subtitle = sprintf("Краскел-Уоллис: p = %.4g", kw$p.value),
       x = NULL, y = "Цена, $")
ggsave(file.path(FIG_DIR, "box_price_genre.png"), p_genre, width = 7, height = 5, dpi = 150)

cat("\nГрафики сохранены в:", FIG_DIR, "\n")
