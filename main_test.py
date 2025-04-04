import unittest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
import os
import pytest
from freezegun import freeze_time

# Импортируем модули из вашего приложения
from main import (
    save_session_record, 
    SessionRecord, 
    generate_daily_report,
    monitor,
    active_sessions,
    report_scheduler,
    init_db
)

class TestOnlineSpy(unittest.TestCase):
    """Тесты для приложения онлайн-шпиона Telegram"""

    def setUp(self):
        """Настройка перед каждым тестом"""
        # Очистка активных сессий перед каждым тестом
        active_sessions.clear()
        
        # Параметры окружения для тестов
        os.environ["API_ID"] = "12345"
        os.environ["API_HASH"] = "test_hash"
        os.environ["APP_NAME"] = "test_app"
        os.environ["USERNAMES"] = "user1,user2,user3"
        os.environ["DATABASE_USER"] = "test_user"
        os.environ["DATABASE_PASSWORD"] = "test_pass"
        os.environ["DATABASE_NAME"] = "test_db"

    @pytest.mark.asyncio
    async def test_init_db(self):
        """Тест инициализации БД"""
        with patch('main.engine') as mock_engine:
            mock_conn = AsyncMock()
            mock_engine.begin.return_value.__aenter__.return_value = mock_conn
            
            await init_db()
            
            # Проверяем, что метод создания таблиц был вызван
            mock_conn.run_sync.assert_called_once()

    @pytest.mark.asyncio
    @patch('main.AsyncSessionLocal')
    async def test_save_session_record(self, mock_session_local):
        """Тест сохранения записи о сессии"""
        # Подготовка данных и моков
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        username = "test_user"
        start_time = datetime.now() - timedelta(minutes=30)
        end_time = datetime.now()
        
        # Вызов тестируемой функции
        await save_session_record(username, start_time, end_time)
        
        # Проверки
        # 1. Проверяем, что сессия была добавлена
        mock_session.add.assert_called_once()
        # 2. Проверяем, что был вызван коммит
        mock_session.commit.assert_called_once()
        
        # Проверяем параметры создания записи
        args, _ = mock_session.add.call_args
        session_record = args[0]
        self.assertEqual(session_record.username, username)
        self.assertEqual(session_record.start_time, start_time)
        self.assertEqual(session_record.end_time, end_time)
        self.assertEqual(session_record.session_date, start_time.date())

    @pytest.mark.asyncio
    @patch('main.client')
    @patch('main.save_session_record')
    async def test_monitor_user_goes_online(self, mock_save_record, mock_client):
        """Тест мониторинга когда пользователь появляется онлайн"""
        # Создаем мок для пользователя и его статуса
        mock_user = MagicMock()
        mock_status = MagicMock()
        mock_status.__class__.__name__ = 'UserStatusOnline'
        mock_user.status = mock_status
        
        # Настраиваем возвращаемое значение для client.get_entity
        mock_client.get_entity.return_value = mock_user
        
        # Запускаем мониторинг на один цикл
        with patch('main.USERNAMES', ['test_user']), \
             patch('asyncio.sleep', AsyncMock()):
            # Выполняем один цикл мониторинга
            monitor_task = asyncio.create_task(monitor())
            await asyncio.sleep(0.1)  # Даем задаче немного времени для выполнения
            monitor_task.cancel()  # Отменяем задачу после выполнения
            
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass  # Ожидаемая ошибка отмены
            
            # Проверяем, что пользователь добавлен в active_sessions
            self.assertIn('test_user', active_sessions)
            # Проверяем, что save_session_record не вызывался (т.к. пользователь только вошел)
            mock_save_record.assert_not_called()

    @pytest.mark.asyncio
    @patch('main.client')
    @patch('main.save_session_record')
    async def test_monitor_user_goes_offline(self, mock_save_record, mock_client):
        """Тест мониторинга когда пользователь выходит из сети"""
        # Подготавливаем данные для пользователя, который уже онлайн
        username = 'test_user'
        start_time = datetime.now() - timedelta(minutes=10)
        active_sessions[username] = start_time
        
        # Создаем мок для пользователя и его статуса (оффлайн)
        mock_user = MagicMock()
        mock_status = MagicMock()
        mock_status.__class__.__name__ = 'UserStatusOffline'
        mock_user.status = mock_status
        
        # Настраиваем возвращаемое значение для client.get_entity
        mock_client.get_entity.return_value = mock_user
        
        # Запускаем мониторинг на один цикл
        with patch('main.USERNAMES', [username]), \
             patch('asyncio.sleep', AsyncMock()):
            # Выполняем один цикл мониторинга
            monitor_task = asyncio.create_task(monitor())
            await asyncio.sleep(0.1)  # Даем задаче немного времени для выполнения
            monitor_task.cancel()  # Отменяем задачу после выполнения
            
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass  # Ожидаемая ошибка отмены
            
            # Проверяем, что пользователь удален из active_sessions
            self.assertNotIn(username, active_sessions)
            # Проверяем, что save_session_record был вызван
            mock_save_record.assert_called_once()
            # Проверяем правильность аргументов при вызове save_session_record
            args, _ = mock_save_record.call_args
            self.assertEqual(args[0], username)
            self.assertEqual(args[1], start_time)  # start_time

    @pytest.mark.asyncio
    @patch('main.AsyncSessionLocal')
    async def test_generate_daily_report(self, mock_session_local):
        """Тест генерации ежедневного отчета"""
        # Создаем тестовые данные
        user1 = "user1"
        user2 = "user2"
        now = datetime.now()
        
        # Создаем записи сессий для тестирования
        session1 = SessionRecord(
            username=user1,
            session_date=now.date(),
            start_time=now - timedelta(hours=1),
            end_time=now - timedelta(minutes=30)
        )
        session2 = SessionRecord(
            username=user1,
            session_date=now.date(),
            start_time=now - timedelta(minutes=20),
            end_time=now - timedelta(minutes=5)
        )
        session3 = SessionRecord(
            username=user2,
            session_date=now.date(),
            start_time=now - timedelta(minutes=45),
            end_time=now - timedelta(minutes=15)
        )
        
        # Настраиваем мок для сессии БД
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Настраиваем возвращаемое значение для запроса
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [session1, session2, session3]
        mock_session.execute.return_value = mock_result
        
        # Вызываем тестируемую функцию
        with patch('main.DEBUG', True):
            report = await generate_daily_report()
        
        # Проверяем, что отчет содержит данные для обоих пользователей
        self.assertIn(user1, report)
        self.assertIn(user2, report)
        self.assertIn("Всего сессий: 2", report)  # Два сеанса для user1
        self.assertIn("Всего сессий: 1", report)  # Один сеанс для user2

    @pytest.mark.asyncio
    @patch('main.client')
    @patch('main.generate_daily_report')
    async def test_report_scheduler(self, mock_generate_report, mock_client):
        """Тест планировщика отчетов"""
        # Подготавливаем мок для генерации отчета
        mock_generate_report.return_value = "Тестовый отчет"
        
        # Настраиваем мок для client.send_message
        mock_client.send_message = AsyncMock()
        mock_client.connect = AsyncMock()
        
        # Запускаем планировщик отчетов на один цикл
        with patch('main.DEBUG', True), \
             patch('asyncio.sleep', AsyncMock()):
            # Выполняем один цикл планировщика
            scheduler_task = asyncio.create_task(report_scheduler())
            await asyncio.sleep(0.1)  # Даем задаче немного времени для выполнения
            scheduler_task.cancel()  # Отменяем задачу после выполнения
            
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass  # Ожидаемая ошибка отмены
            
            # Проверяем, что отчет был сгенерирован
            mock_generate_report.assert_called_once()
            # Проверяем, что отчет был отправлен
            mock_client.send_message.assert_called_once_with('me', "Тестовый отчет")

if __name__ == "__main__":
    unittest.main()