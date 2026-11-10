# =============================================================================
# Steam Forecast: R Shiny приложение для прогноза отзывов, продаж и выручки
# -----------------------------------------------------------------------------
# Требует обученную модель: Rscript src/train_models.R  ->  models/model_rf.rds
# Запуск:  shiny::runApp("src")   (из корня проекта)
#      или Rscript -e "shiny::runApp('src')"
#
# Логика прогноза:
#   1) модель предсказывает log1p(total_reviews) -> отзывы = expm1(prediction)
#   2) правило Бокслейтера (Boxleiter): проданные копии = отзывы * 30
#   3) выручка (грубая оценка, без скидок и комиссии Steam) = копии * цена
# =============================================================================

for (pkg in c("shiny", "ggplot2", "randomForest")) {
  if (!requireNamespace(pkg, quietly = TRUE)) {
    stop(sprintf("Не установлен пакет '%s'. Выполните install.packages('%s').", pkg, pkg))
  }
}
library(shiny)
library(ggplot2)
library(randomForest)

# ---- Загрузка модели ---------------------------------------------------------
# Поднимаемся вверх от рабочей папки (src/ или корень) до каталога с models/model_rf.rds
find_model_path <- function() {
  dir <- normalizePath(getwd(), winslash = "/")
  repeat {
    candidate <- file.path(dir, "models", "model_rf.rds")
    if (file.exists(candidate)) return(candidate)
    parent <- dirname(dir)
    if (identical(parent, dir)) {
      stop("Файл models/model_rf.rds не найден. Сначала запустите src/train_models.R.")
    }
    dir <- parent
  }
}

bundle     <- readRDS(find_model_path())
model      <- bundle$model
features   <- bundle$features
medians    <- bundle$medians
ranges     <- bundle$ranges
importance <- bundle$importance

BOXLEITER_MULTIPLIER <- 30

# Жанры: подпись в интерфейсе -> имя признака в модели
GENRES <- c(
  "Action"     = "genre_action",
  "RPG"        = "genre_rpg",
  "Strategy"   = "genre_strategy",
  "Adventure"  = "genre_adventure",
  "Indie"      = "genre_indie",
  "Simulation" = "genre_simulation"
)

# Читаемый формат чисел для карточек
fmt_int <- function(x) format(round(x), big.mark = " ", scientific = FALSE, trim = TRUE)
fmt_usd <- function(x) paste0("$", format(round(x), big.mark = " ", scientific = FALSE, trim = TRUE))

# Карточка с показателем
metric_card <- function(title, value_output_id, hint, color) {
  div(class = "metric-card", style = sprintf("border-top: 4px solid %s;", color),
      div(class = "metric-title", title),
      div(class = "metric-value", textOutput(value_output_id, inline = TRUE)),
      div(class = "metric-hint", hint))
}

# ---- UI ----------------------------------------------------------------------
ui <- fluidPage(
  title = "Steam Forecast",
  tags$head(tags$style(HTML("
    body { background:#f4f6f8; }
    .app-title { background:#1b2838; color:#fff; padding:18px 24px; margin:0 -15px 20px -15px; }
    .app-title h2 { margin:0; font-size:26px; }
    .app-title p  { margin:4px 0 0 0; color:#a3b3c2; }
    .metric-card { background:#fff; border-radius:8px; padding:16px; margin-bottom:16px;
                   box-shadow:0 1px 4px rgba(0,0,0,.12); }
    .metric-title { color:#6b7a89; font-size:13px; text-transform:uppercase; letter-spacing:.5px; }
    .metric-value { font-size:30px; font-weight:700; color:#1b2838; margin:6px 0; }
    .metric-hint  { color:#8f98a0; font-size:12px; }
    .well { background:#fff; }
  "))),

  div(class = "app-title",
      h2("Steam Forecast"),
      p("Прогноз отзывов, продаж и выручки игры по её характеристикам")),

  sidebarLayout(
    sidebarPanel(
      width = 4,
      h4("Параметры игры"),
      sliderInput("price", "Цена, $",
                  min = 0, max = max(60, ceiling(ranges$price[2])),
                  value = 19.99, step = 0.5),
      sliderInput("release_year", "Год релиза",
                  min = ranges$release_year[1], max = ranges$release_year[2] + 1,
                  value = min(2025, ranges$release_year[2]), step = 1, sep = ""),
      sliderInput("languages_count", "Количество языков",
                  min = 1, max = max(30, ranges$languages_count[2]),
                  value = 8, step = 1),
      hr(),
      checkboxInput("is_multiplayer",  "Мультиплеер",   value = FALSE),
      checkboxInput("is_coop",         "Кооператив",    value = FALSE),
      checkboxInput("is_singleplayer", "Одиночная игра", value = TRUE),
      hr(),
      checkboxGroupInput("genres", "Жанры", choices = names(GENRES), selected = "Action")
    ),

    mainPanel(
      width = 8,
      fluidRow(
        column(4, metric_card("Прогноз отзывов", "out_reviews", "expm1 от прогноза модели", "#66c0f4")),
        column(4, metric_card("Проданные копии", "out_copies", "Boxleiter: отзывы × 30", "#5ba32b")),
        column(4, metric_card("Выручка", "out_revenue", "копии × цена", "#e4a11b"))
      ),
      wellPanel(
        h4("Важность признаков (Random Forest, %IncMSE)"),
        plotOutput("importance_plot", height = "420px")
      ),
      p(style = "color:#8f98a0; font-size:12px;",
        "Оценка ориентировочная: модель обучена на небольшой выборке игр Steam, ",
        "правило Бокслейтера (30 копий на отзыв) — грубое приближение.")
    )
  )
)

# ---- Server ------------------------------------------------------------------
server <- function(input, output, session) {

  # Собираем строку признаков в том порядке, который ожидает модель
  new_game <- reactive({
    row <- as.list(medians)  # значения по умолчанию для признаков, которых нет в UI
    row$price           <- input$price
    row$release_year    <- input$release_year
    row$languages_count <- input$languages_count
    row$is_multiplayer  <- as.numeric(input$is_multiplayer)
    row$is_coop         <- as.numeric(input$is_coop)
    row$is_singleplayer <- as.numeric(input$is_singleplayer)
    for (genre_label in names(GENRES)) {
      row[[GENRES[[genre_label]]]] <- as.numeric(genre_label %in% input$genres)
    }
    as.data.frame(row[features], check.names = FALSE)
  })

  # Прогноз числа отзывов (модель работает в шкале log1p)
  predicted_reviews <- reactive({
    pred_log <- as.numeric(predict(model, newdata = new_game()))
    max(expm1(pred_log), 0)
  })

  predicted_copies  <- reactive(predicted_reviews() * BOXLEITER_MULTIPLIER)
  predicted_revenue <- reactive(predicted_copies() * input$price)

  output$out_reviews <- renderText(fmt_int(predicted_reviews()))
  output$out_copies  <- renderText(fmt_int(predicted_copies()))
  output$out_revenue <- renderText(fmt_usd(predicted_revenue()))

  # График важности признаков (данные сохранены при обучении)
  output$importance_plot <- renderPlot({
    imp <- importance[order(importance$importance), ]
    imp$feature <- factor(imp$feature, levels = imp$feature)
    ggplot(imp, aes(x = feature, y = importance)) +
      geom_col(fill = "#2a475e") +
      coord_flip() +
      labs(x = NULL, y = "%IncMSE") +
      theme_minimal(base_size = 14)
  })
}

shinyApp(ui = ui, server = server)
